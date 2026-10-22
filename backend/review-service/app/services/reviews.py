"""Sharh oqimi (C11). QOIDALAR (monolit `api/booking/reviews.py`):

* faqat yakunlangan bron egasi, yakunlanganidan keyin `sla.review_window_days` (admin, standart 30) ichida;
* bitta bronga bitta sharh (UNIQUE booking_id; parallel so'rov — 409, 500 emas);
* avto-filtr bayroq qo'ysa — `pending` (reytingga ta'sir qilmaydi), aks holda darhol `published`;
* shifokor javobi — bir marta, toza matn; moderator `pending` ni publish/reject qiladi, `published` ni
  shikoyat bo'yicha olib tashlay oladi (monolitda imkon yo'q edi) — har holatda reyting qayta hisoblanadi.

Egalik: `booking.completed` proyeksiyasi; proyeksiya hali kelmagan bo'lsa (event kechikishi) — booking
`review-eligibility` (sinxron fallback).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction
from medbron_common.eventbus.envelope import new_event_id
from medbron_common.security import CurrentAdmin

from app.core.config import get_settings
from app.events import outbox
from app.metrics import ELIGIBILITY_FALLBACK, FLAGS, MODERATED, REVIEWS
from app.models import Review
from app.models.review import PENDING, PUBLISHED, REJECTED
from app.repositories import reviews as repo
from app.services import moderation, payloads, ratings
from app.services.deps import Deps
from app.services.errors import (
    AlreadyReviewed,
    BookingNotFound,
    InvalidReply,
    InvalidTransition,
    NotReviewable,
    ReviewNotFound,
)


@dataclass(frozen=True)
class ReviewInput:
    booking_id: uuid.UUID
    rating: int
    comment: str = ""
    is_anonymous: bool = False
    author_name: str = ""


@dataclass(frozen=True)
class _Target:
    doctor_id: uuid.UUID
    clinic_id: uuid.UUID | None
    number: str
    completed_at: datetime


async def _target(session: AsyncSession, deps: Deps, booking_id: uuid.UUID, client_id: uuid.UUID) -> _Target:
    row = await repo.reviewable(session, booking_id)
    if row is not None:
        if row.client_id != client_id:
            raise BookingNotFound()
        return _Target(row.doctor_id, row.clinic_id, row.number, row.completed_at)
    # Proyeksiya kechikkan — booking'dan so'raymiz (oyna tekshiruvi pastda, bizning sozlama bilan)
    e = await deps.booking.review_eligibility(booking_id, client_id)
    ELIGIBILITY_FALLBACK.labels(result=e.reason or "eligible").inc()
    if e.reason == "not_owner":
        raise BookingNotFound()
    if e.doctor_id is None or e.completed_at is None:
        raise NotReviewable()
    return _Target(e.doctor_id, None, "", e.completed_at)


async def create(session: AsyncSession, deps: Deps, *, client_id: uuid.UUID, data: ReviewInput) -> Review:
    target = await _target(session, deps, data.booking_id, client_id)
    window = timedelta(days=int((await deps.settings.get())["sla"]["review_window_days"]))
    if datetime.now(UTC) - target.completed_at > window:
        raise NotReviewable(f"Sharh yozish muddati o'tgan ({window.days} kun)", code="REVIEW_WINDOW_CLOSED")
    comment = data.comment.strip()[: get_settings().max_comment_length]
    found = moderation.flags(f"{comment} {data.author_name}")
    review = Review(
        id=new_event_id(),
        booking_id=data.booking_id,
        booking_number=target.number,
        client_id=client_id,
        doctor_id=target.doctor_id,
        clinic_id=target.clinic_id,
        rating=data.rating,
        comment=comment,
        is_anonymous=data.is_anonymous,
        author_name=moderation.short_name(data.author_name.strip()),
        status=PENDING if found else PUBLISHED,
        moderation_flags=found,
    )
    try:
        async with transaction(session):
            if await repo.by_booking(session, data.booking_id) is not None:
                raise AlreadyReviewed()
            session.add(review)
            await session.flush()
            payloads.submitted(session, review)
            if review.status == PUBLISHED:
                payloads.published(session, review)
                await ratings.recalculate(session, doctor_id=review.doctor_id, clinic_id=review.clinic_id)
    except IntegrityError:
        raise AlreadyReviewed() from None  # parallel ikkinchi so'rov
    REVIEWS.labels(status=review.status).inc()
    for flag in found:
        FLAGS.labels(flag=flag).inc()
    return review


async def reply(session: AsyncSession, *, doctor_id: uuid.UUID, review_id: uuid.UUID, text: str) -> Review:
    text = text.strip()
    if not text:
        raise InvalidReply("Javob bo'sh bo'lmasligi kerak")
    if moderation.flags(text):
        raise InvalidReply("Javobda telefon, havola yoki nomaqbul so'z bo'lmasligi kerak")
    async with transaction(session):
        r = await repo.get(session, review_id, for_update=True)
        if r is None or r.doctor_id != doctor_id or r.status != PUBLISHED:
            raise ReviewNotFound()
        if r.doctor_replied_at is not None:
            raise InvalidTransition("Sharhga faqat bir marta javob berish mumkin", code="ALREADY_REPLIED")
        r.doctor_reply = text[: get_settings().max_comment_length]
        r.doctor_replied_at = datetime.now(UTC)
    return r


async def moderate(
    session: AsyncSession, admin: CurrentAdmin, review_id: uuid.UUID, *, publish: bool, reason: str, ip: str | None
) -> Review:
    async with transaction(session):
        r = await repo.get(session, review_id, for_update=True)
        if r is None:
            raise ReviewNotFound()
        before = r.status
        allowed = (PENDING,) if publish else (PENDING, PUBLISHED)
        if r.status not in allowed:
            raise InvalidTransition("Bu holatdagi sharhga bu amal qo'llanmaydi")
        r.status = PUBLISHED if publish else REJECTED
        r.moderated_by, r.moderated_at = admin.admin_id, datetime.now(UTC)
        if not publish:
            r.reject_reason = reason.strip()[:500]
            payloads.rejected(session, r)
        else:
            payloads.published(session, r)
        # pending → rejected reytingga ta'sir qilmaydi; qolgan o'tishlar — ta'sir qiladi
        if before == PUBLISHED or publish:
            await ratings.recalculate(session, doctor_id=r.doctor_id, clinic_id=r.clinic_id)
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="review.publish" if publish else "review.reject",
            object_type="review",
            object_id=r.id,
            before={"status": before},
            after={"status": r.status, **({"reason": r.reject_reason} if not publish else {})},
            ip=ip,
        )
    MODERATED.labels(decision=r.status).inc()
    return r


async def on_booking_completed(session: AsyncSession, data: dict[str, Any]) -> None:
    """Proyeksiya (idempotent). Sharh fallback bilan oldinroq yozilgan bo'lsa — klinika to'ldiriladi."""
    booking_id = uuid.UUID(data["booking_id"])
    clinic_id = uuid.UUID(data["clinic_id"]) if data.get("clinic_id") else None
    await repo.upsert_reviewable(
        session,
        {
            "booking_id": booking_id,
            "number": data["number"],
            "client_id": uuid.UUID(data["client_id"]),
            "doctor_id": uuid.UUID(data["doctor_id"]),
            "clinic_id": clinic_id,
            "completed_at": datetime.fromisoformat(data["completed_at"]),
        },
    )
    if clinic_id is not None:
        r = await repo.fill_clinic(session, booking_id, clinic_id)
        if r is not None and r.status == PUBLISHED:
            await ratings.recalculate(session, doctor_id=r.doctor_id, clinic_id=clinic_id)
