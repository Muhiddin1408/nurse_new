"""
booking/reviews.py — sharh va reyting oqimi (C11).

    POST /api/v1/booking/bookings/{id}/review          mijoz
    GET  /api/v1/catalog/doctors/{id}/reviews          ochiq
    GET  /api/v1/doctor/reviews                        shifokor
    POST /api/v1/doctor/reviews/{id}/reply             shifokor (bir marta)
    GET  /api/v1/moderation/reviews?status=pending     admin
    POST /api/v1/moderation/reviews/{id}/approve|reject

QOIDALAR:
    * faqat `completed` bronga, yakunlanganidan keyin 30 kun ichida;
    * bitta bronga bitta sharh (OneToOne);
    * avtomatik filtr (haqorat, telefon, havola/reklama) — shubhali sharh
      `pending` ga tushadi va reytingga ta'sir qilmaydi, admin ko'radi;
    * reyting faqat `published` sharhlardan; < 5 ta sharh bo'lsa ko'rsatilmaydi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone

from api import audit
from api.catalog import services as catalog_services
from api.events import services as events
from apps.booking.models import Booking, Review

REVIEW_WINDOW = timedelta(days=30)
MIN_REVIEWS_FOR_RATING = 5
MAX_COMMENT_LENGTH = 2000


class ReviewError(Exception):
    """Biznes qoidasi buzildi (409)."""


class ReviewNotFound(ReviewError):
    pass


# ---------------------------------------------------------------------------
# Avtomatik moderatsiya — sof funksiya
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"(?:\+?998[\s\-()]*)?\b\d{2}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b")
_LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|@[A-Za-z0-9_]{4,}|\b[\w-]+\.(?:uz|com|ru|net|org)\b)", re.I
)
# Qisqa ro'yxat — maqsad hammasini ushlash emas, eng ko'p uchraydiganini
# `pending` ga tushirish: yakuniy qarorni odam qiladi.
_BAD_WORDS = (
    "ahmoq", "tentak", "iflos", "haromi", "jalab", "qanjiq", "itvachcha",
    "дурак", "идиот", "тупой", "сука", "бля", "урод", "мразь",
)
_BAD_RE = re.compile(r"\b(" + "|".join(map(re.escape, _BAD_WORDS)) + r")", re.I)


def moderation_flags(text: str) -> list[str]:
    """Matnda nima shubhali: 'profanity' | 'phone' | 'link'. Bo'sh ro'yxat — toza."""
    flags = []
    if _BAD_RE.search(text):
        flags.append("profanity")
    if _PHONE_RE.search(text):
        flags.append("phone")
    if _LINK_RE.search(text):
        flags.append("link")
    return flags


def display_rating(rating, reviews_count: int):
    """1 ta 5 ballik sharh — 5.0 reyting emas: kam sharhda reyting yashiriladi."""
    return rating if reviews_count >= MIN_REVIEWS_FOR_RATING else None


# ---------------------------------------------------------------------------
# Mijoz
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReviewInput:
    rating: int
    comment: str = ""
    is_anonymous: bool = False


def create_review(*, client_id: UUID, booking_id: UUID, data: ReviewInput) -> Review:
    booking = Booking.objects.filter(id=booking_id, client_id=client_id).first()
    if booking is None:
        raise ReviewNotFound("not_found")
    if booking.status != Booking.Status.COMPLETED:
        raise ReviewError("Faqat yakunlangan qabulga sharh yozish mumkin")
    finished_at = booking.completed_at or booking.updated_at
    if timezone.now() - finished_at > REVIEW_WINDOW:
        raise ReviewError("Sharh yozish muddati o'tgan (30 kun)")
    if not 1 <= data.rating <= 5:
        raise ReviewError("Baho 1 dan 5 gacha bo'lishi kerak")

    comment = data.comment.strip()[:MAX_COMMENT_LENGTH]
    flags = moderation_flags(comment)

    with transaction.atomic():
        # Bron qatorini qulflaymiz: bitta bronga ikki parallel so'rov —
        # ikkinchisi UNIQUE xatosi (500) o'rniga tushunarli 409 oladi
        Booking.objects.select_for_update().filter(id=booking.id).first()
        if Review.objects.filter(booking_id=booking.id).exists():
            raise ReviewError("Bu qabulga sharh allaqachon yozilgan")
        review = Review.objects.create(
            booking=booking,
            doctor_id=booking.doctor_id,
            client_id=client_id,
            rating=data.rating,
            comment=comment,
            is_anonymous=data.is_anonymous,
            status=Review.Status.PENDING if flags else Review.Status.PUBLISHED,
            moderation_flags=flags,
        )
        if review.status == Review.Status.PUBLISHED:
            _on_published(review)
    return review


# ---------------------------------------------------------------------------
# Shifokor
# ---------------------------------------------------------------------------


def doctor_reviews(doctor_id: UUID, *, limit: int, offset: int) -> list[Review]:
    return list(
        Review.objects.filter(doctor_id=doctor_id, status=Review.Status.PUBLISHED)
        .select_related("client", "booking")
        .order_by("-created_at")[offset : offset + limit]
    )


def reply(*, doctor_id: UUID, review_id: UUID, text: str) -> Review:
    text = text.strip()
    if not text:
        raise ReviewError("Javob bo'sh bo'lmasligi kerak")
    if moderation_flags(text):
        raise ReviewError("Javobda telefon, havola yoki nomaqbul so'z bo'lmasligi kerak")
    with transaction.atomic():
        review = (
            Review.objects.select_for_update()
            .filter(id=review_id, doctor_id=doctor_id, status=Review.Status.PUBLISHED)
            .first()
        )
        if review is None:
            raise ReviewNotFound("not_found")
        if review.doctor_replied_at is not None:
            raise ReviewError("Sharhga faqat bir marta javob berish mumkin")
        review.doctor_reply = text[:MAX_COMMENT_LENGTH]
        review.doctor_replied_at = timezone.now()
        review.save(update_fields=["doctor_reply", "doctor_replied_at", "updated_at"])
    return review


# ---------------------------------------------------------------------------
# Moderatsiya
# ---------------------------------------------------------------------------


def pending_reviews(*, limit: int, offset: int) -> list[Review]:
    return list(
        Review.objects.filter(status=Review.Status.PENDING)
        .select_related("client", "booking")
        .order_by("created_at")[offset : offset + limit]
    )


def moderate(*, review_id: UUID, approve: bool, admin_id: UUID) -> Review:
    with transaction.atomic():
        review = Review.objects.select_for_update().filter(id=review_id).first()
        if review is None:
            raise ReviewNotFound("not_found")
        if review.status != Review.Status.PENDING:
            raise ReviewError("Sharh moderatsiyada emas")
        review.status = Review.Status.PUBLISHED if approve else Review.Status.REJECTED
        review.moderated_by_id = admin_id
        review.moderated_at = timezone.now()
        review.save(update_fields=["status", "moderated_by", "moderated_at", "updated_at"])
        audit.record("review.moderate", obj=review, before={"status": Review.Status.PENDING},
                     after={"status": review.status})
        if approve:
            _on_published(review)
    return review


# ---------------------------------------------------------------------------
# Reyting
# ---------------------------------------------------------------------------


def recalculate_doctor_rating(doctor_id: UUID) -> tuple[Decimal, int]:
    """`Doctor.rating` / `reviews_count` — faqat e'lon qilingan sharhlardan.

    Qo'lda kiritilgan reyting shu bilan yo'qoladi: endi raqamning manbai bor."""
    agg = Review.objects.filter(doctor_id=doctor_id, status=Review.Status.PUBLISHED).aggregate(
        avg=Avg("rating"), count=Count("id")
    )
    count = agg["count"] or 0
    avg = Decimal(str(agg["avg"] or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    catalog_services.set_doctor_rating(doctor_id, rating=avg, reviews_count=count)
    return avg, count


def _on_published(review: Review) -> None:
    """Chaqiruvchining tranzaksiyasi ICHIDA: reyting + shifokorga xabar (outbox)."""
    recalculate_doctor_rating(review.doctor_id)
    events.publish(
        topic="doctor.notifications",
        key=str(review.doctor_id),
        event_type="ReviewPublished",
        payload={
            "doctor_id": str(review.doctor_id),
            "review_id": str(review.id),
            "rating": review.rating,
            "booking_number": review.booking.number,
        },
    )


# ---------------------------------------------------------------------------
# Ko'rinish
# ---------------------------------------------------------------------------


def _author(review: Review) -> str | None:
    if review.is_anonymous:
        return None
    # Ochiq sahifada to'liq ism emas — "Aziz T." (shaxsiy ma'lumot)
    parts = (review.client.full_name or "").split()
    if not parts:
        return None
    return parts[0] + (f" {parts[1][0]}." if len(parts) > 1 else "")


def serialize(review: Review, *, public: bool = True) -> dict:
    data = {
        "id": str(review.id),
        "rating": review.rating,
        "comment": review.comment,
        "author": _author(review),
        "is_anonymous": review.is_anonymous,
        "created_at": review.created_at.isoformat(),
        "doctor_reply": review.doctor_reply or None,
        "doctor_replied_at": review.doctor_replied_at.isoformat() if review.doctor_replied_at else None,
    }
    if not public:
        data.update(
            status=review.status,
            moderation_flags=review.moderation_flags,
            booking_number=review.booking.number,
            doctor_id=str(review.doctor_id),
        )
    return data
