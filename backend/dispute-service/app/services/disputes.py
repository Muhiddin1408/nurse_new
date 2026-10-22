"""Nizolar oqimi (B11, ARCHITECTURE 6.7).

Mijoz `confirmed/in_progress/completed/no_show` bron bo'yicha nizo ochadi (egalik va holat — booking
`/summary`, sinxron) → `dispute.opened` (SLA admin `sla.dispute_hours`) → admin hal qiladi →
`dispute.resolved{refund_tiyin, doctor_liable}` → payment refund qiladi (`doctor_liable=false` — shifokor
ulushi 0, billing ushlamaydi) → `refund.succeeded{source=dispute}` → nizo yopiladi.

Monolitdan farqlar (xatolar tuzatildi): SLA kodda qotirilmagan (admin sozlamasi) va uning buzilishi
kuzatiladi; bitta bronga bitta nizo (yopilgandan keyin qayta ochib, qayta refund so'rab bo'lmaydi);
"shifokor foydasiga" emas — kim javobgarligi (`doctor_liable`) alohida; refund ≤ to'langan summa tekshiriladi;
refund pul qaytgach yopiladi (monolitda refund navbatga tushishi bilan "hal qilindi" bo'lardi).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction
from medbron_common.eventbus.envelope import new_event_id
from medbron_common.security import CurrentAdmin

from app import metrics
from app.clients.upstream import FileRejected
from app.core.config import get_settings
from app.events import outbox
from app.models import Dispute
from app.models.dispute import (
    CLOSED,
    DISPUTABLE_BOOKING_STATUSES,
    OPEN,
    REFUND_FULL,
    REFUND_PARTIAL,
    REJECTED,
    RESOLVED,
)
from app.repositories import disputes as repo
from app.services import payloads
from app.services.deps import Deps
from app.services.errors import (
    BookingNotFound,
    DisputeExists,
    DisputeNotFound,
    EvidenceRejected,
    InvalidResolution,
    InvalidState,
    NotDisputable,
    TooManyEvidence,
)

logger = logging.getLogger(__name__)

SLA_BATCH = 500


def owner_ref(dispute_id: uuid.UUID) -> str:
    return f"dispute:{dispute_id}"


async def open_dispute(
    session: AsyncSession, deps: Deps, *, client_id: uuid.UUID, booking_id: uuid.UUID, reason: str, description: str
) -> Dispute:
    booking = await deps.booking.summary(booking_id, client_id)
    if booking is None:
        raise BookingNotFound()
    if booking["status"] not in DISPUTABLE_BOOKING_STATUSES:
        raise NotDisputable()
    hours = int((await deps.settings.get())["sla"]["dispute_hours"])
    now = datetime.now(UTC)
    d = Dispute(
        id=new_event_id(),
        booking_id=booking_id,
        booking_number=booking["number"],
        client_id=client_id,
        doctor_id=uuid.UUID(booking["doctor_id"]),
        paid_tiyin=int(booking["paid_tiyin"]),
        reason=reason,
        description=description,
        status=OPEN,
        due_at=now + timedelta(hours=hours),
    )
    try:
        async with transaction(session):
            if await repo.by_booking(session, booking_id) is not None:
                raise DisputeExists()
            session.add(d)
            await session.flush()  # uq_disputes_booking — parallel so'rovda shu yerda
            payloads.opened(session, d)
    except IntegrityError:
        raise DisputeExists() from None
    metrics.OPENED.labels(reason=reason).inc()
    return d


async def owned(session: AsyncSession, dispute_id: uuid.UUID, client_id: uuid.UUID) -> Dispute:
    d = await repo.get(session, dispute_id)
    if d is None or d.client_id != client_id:
        raise DisputeNotFound()
    return d


async def add_evidence(
    session: AsyncSession, deps: Deps, *, dispute_id: uuid.UUID, client_id: uuid.UUID, file_id: uuid.UUID
) -> Dispute:
    """Faqat nizo egasi va faqat ochiq nizoga. Fayl file-service'da shu userga tegishli va tekshirilgan bo'lishi
    shart (biriktirish idempotent — takror so'rov o'sha natija)."""
    d = await owned(session, dispute_id, client_id)
    if d.status != OPEN:
        raise InvalidState("Nizo yopilgan — dalil qo'shib bo'lmaydi")
    existing = {e.file_id for e in (await repo.evidence_of(session, [d.id]))[d.id]}
    if file_id not in existing and len(existing) >= get_settings().max_evidence_files:
        raise TooManyEvidence(f"Ko'pi bilan {get_settings().max_evidence_files} ta fayl")
    await session.rollback()  # tarmoq chaqiruvi paytida tranzaksiya ochiq turmasin
    try:
        file = await deps.files.attach(file_id, owner_user_id=client_id, owner_ref=owner_ref(dispute_id))
    except FileRejected:
        raise EvidenceRejected() from None
    async with transaction(session):
        locked = await repo.get(session, dispute_id, for_update=True)
        if locked is None or locked.status != OPEN:
            raise InvalidState("Nizo yopilgan — dalil qo'shib bo'lmaydi")
        await repo.add_evidence(
            session,
            dispute_id=dispute_id,
            file_id=file_id,
            added_by=client_id,
            content_type=str(file.get("content_type", "")),
        )
    return locked


@dataclass(frozen=True)
class Resolution:
    resolution: str
    refund_tiyin: int
    doctor_liable: bool
    note: str


def validate(d: Dispute, r: Resolution) -> int:
    """Qaytaradi: refund summasi. `refund_full` — to'langan summaning hammasi (summa yuborilmasa ham)."""
    if r.resolution == REJECTED:
        if r.refund_tiyin or r.doctor_liable:
            raise InvalidResolution("Rad etilganda refund va shifokor javobgarligi bo'lmaydi")
        return 0
    if d.paid_tiyin <= 0:
        raise InvalidResolution("Bron onlayn to'lanmagan — qaytariladigan pul yo'q (klinikada to'langan)")
    if r.resolution == REFUND_FULL:
        if r.refund_tiyin not in (0, d.paid_tiyin):
            raise InvalidResolution("refund_full — summa to'langan summaga teng bo'ladi")
        return d.paid_tiyin
    if r.resolution == REFUND_PARTIAL:
        if not 0 < r.refund_tiyin < d.paid_tiyin:
            raise InvalidResolution(f"refund_partial: 0 < summa < {d.paid_tiyin} (to'langan)")
        return r.refund_tiyin
    raise InvalidResolution("resolution: refund_full | refund_partial | rejected")


async def resolve(
    session: AsyncSession, admin: CurrentAdmin, dispute_id: uuid.UUID, r: Resolution, *, ip: str | None
) -> Dispute:
    """IDEMPOTENT: allaqachon hal qilingan nizo o'sha qaror bilan qaytariladi (admin ikki marta bosdi).
    Boshqa qaror bilan qayta hal qilish — 409."""
    async with transaction(session):
        d = await repo.get(session, dispute_id, for_update=True)
        if d is None:
            raise DisputeNotFound()
        if d.status != OPEN:
            if d.resolution == r.resolution and d.doctor_liable == r.doctor_liable:
                return d
            raise InvalidState("Nizo allaqachon hal qilingan")
        refund = validate(d, r)
        now = datetime.now(UTC)
        d.resolution = r.resolution
        d.refund_tiyin = refund
        d.doctor_liable = r.doctor_liable
        d.resolution_note = r.note
        d.resolved_by = admin.admin_id
        d.resolved_at = now
        d.status = RESOLVED if refund > 0 else CLOSED
        if refund == 0:
            d.closed_at = now
        payloads.resolved(session, d)
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="dispute.resolve",
            object_type="dispute",
            object_id=d.id,
            before={"status": OPEN},
            after={
                "resolution": r.resolution,
                "refund_tiyin": refund,
                "doctor_liable": r.doctor_liable,
                "note": r.note[:500],
            },
            ip=ip,
        )
    metrics.RESOLVED.labels(resolution=r.resolution, doctor_liable=str(r.doctor_liable).lower()).inc()
    if d.status == CLOSED:
        metrics.CLOSED.labels(resolution=r.resolution).inc()
    return d


async def on_refund_succeeded(session: AsyncSession, data: dict[str, Any]) -> None:
    """payment pulni qaytardi. Faqat `source=dispute` (boshqa manbalar — bekor qilish, no-show — bizniki emas)."""
    if data["source"] != "dispute" or not data.get("source_ref"):
        return
    d = await repo.get(session, uuid.UUID(data["source_ref"]), for_update=True)
    if d is None:
        logger.error("refund for unknown dispute", extra={"dispute_id": data["source_ref"]})
        return
    if d.status == CLOSED and d.refund_id is not None:
        return  # takror
    d.refund_id = uuid.UUID(data["refund_id"])
    d.refunded_tiyin = int(data["amount_tiyin"])  # payment qoldiq bilan cheklagan bo'lishi mumkin
    d.status = CLOSED
    d.closed_at = datetime.now(UTC)
    if d.refunded_tiyin < d.refund_tiyin:
        logger.warning(
            "dispute refund smaller than decided",
            extra={"dispute_id": str(d.id), "decided": d.refund_tiyin, "refunded": d.refunded_tiyin},
        )
    metrics.CLOSED.labels(resolution=d.resolution or "").inc()


async def check_sla(factory: async_sessionmaker[AsyncSession]) -> int:
    """SLA muddati o'tgan ochiq nizolar — `dispute.sla_breached` (har nizo uchun BIR marta). SKIP LOCKED."""
    total = 0
    while True:
        async with factory() as session, session.begin():
            rows = await repo.sla_due(session, datetime.now(UTC), limit=SLA_BATCH)
            for d in rows:
                d.sla_breached_at = datetime.now(UTC)
                payloads.sla_breached(session, d)
                metrics.SLA_BREACHED.inc()
                logger.error(
                    "ALERT: dispute SLA breached", extra={"dispute_id": str(d.id), "due_at": d.due_at.isoformat()}
                )
        total += len(rows)
        if len(rows) < SLA_BATCH:
            return total
