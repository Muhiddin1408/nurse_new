"""Bron holat mashinasi (ARCHITECTURE 7.1):

    pending_payment → confirmed (to'lov) | expired (muddat) | cancelled
    confirmed → in_progress (shifokor boshladi) → completed | no_show
    confirmed → cancelled (siyosat bo'yicha) | completed | no_show

Har o'tish: `SELECT ... FOR UPDATE` (ADR-0011 #7), holat + event BITTA tranzaksiyada. Yakuniy holatga qayta
o'tish — idempotent (o'zgarishsiz qaytadi, event yo'q). Slot bo'shatish — schedule `booking.*` eventlaridan.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.events import Events

from app.clients.upstream import UpstreamRejected
from app.core.config import get_settings
from app.events import outbox
from app.metrics import CONFIRMATION_FAILED, TRANSITIONS
from app.models import Booking
from app.models.booking import (
    CANCELLED,
    COMPLETED,
    CONFIRMED,
    EXPIRED,
    FINAL_STATUSES,
    IN_PROGRESS,
    NO_SHOW,
    PENDING_PAYMENT,
)
from app.repositories import bookings as repo
from app.services import payloads, policy
from app.services.deps import Deps
from app.services.errors import BookingNotFound, InvalidBookingRequest, InvalidTransition, SlotTaken

logger = logging.getLogger(__name__)


def paid_net(b: Booking) -> int:
    """Qaytariladigan summa asosi: to'langan − allaqachon qaytarilgan."""
    return max(0, b.paid_tiyin - b.refunded_tiyin)


async def expire(session: AsyncSession, b: Booking, now: datetime) -> bool:
    if b.status != PENDING_PAYMENT:
        return False
    b.status = EXPIRED
    b.cancelled_at = now
    b.cancel_reason_code = "payment_timeout"
    b.cancel_reason_text = "To'lov muddati o'tdi"
    payloads.expired(session, b)
    TRANSITIONS.labels(to=EXPIRED).inc()
    return True


async def expire_stale_on_slot(session: AsyncSession, slot_id: uuid.UUID, now: datetime) -> None:
    """Lazy expiry (E8): slot yangi bronga berilganda, shu slotdagi muddati o'tgan pending bron yopiladi."""
    stale = await session.scalars(
        select(Booking)
        .where(Booking.slot_id == slot_id, Booking.status == PENDING_PAYMENT, Booking.payment_due_at < now)
        .with_for_update()
    )
    for b in stale.all():
        await expire(session, b, now)


async def expire_due(session: AsyncSession, *, limit: int = 500) -> int:
    """Har daqiqa (scheduler): muddati o'tgan to'lanmagan bronlar. SKIP LOCKED — parallel nusxalar xavfsiz."""
    now = datetime.now(UTC)
    rows = await repo.due_unpaid(session, now, limit)
    for b in rows:
        await expire(session, b, now)
    await session.commit()
    return len(rows)


async def cancel(
    session: AsyncSession,
    b: Booking,
    *,
    actor: str,
    reason_code: str | None,
    reason_text: str,
    platform: dict[str, Any],
    actor_id: uuid.UUID | None = None,
    ip: str | None = None,
    force_full_refund: bool = False,
) -> Booking:
    """`b` — FOR UPDATE bilan olingan. Yakuniy holat — o'zgarishsiz (monolit: 200, event yo'q)."""
    if b.status in FINAL_STATUSES:
        return b
    now = datetime.now(UTC)
    status_for_policy = CONFIRMED if b.status == IN_PROGRESS else b.status
    outcome = policy.evaluate_cancellation(
        status=status_for_policy,
        start_at=b.start_at,
        paid_tiyin=paid_net(b),
        actor=actor,
        now=now,
        policy=platform["cancellation"],
    )
    if force_full_refund:
        outcome = policy.CancellationOutcome(True, paid_net(b), 0, reason_code or "system_cancelled")
    elif not outcome.allowed:
        message = (
            "Qabul boshlangan — bekor qilib bo'lmaydi"
            if outcome.reason_code == "already_started"
            else ("Bu bronni bekor qilib bo'lmaydi")
        )
        raise InvalidTransition(message, code=outcome.reason_code.upper())
    previous = b.status
    b.status = CANCELLED
    b.cancelled_at = now
    b.cancelled_by = actor
    b.cancel_reason_code = reason_code or outcome.reason_code
    b.cancel_reason_text = reason_text
    payloads.cancelled(
        session, b, previous_status=previous, refund_tiyin=outcome.refund_tiyin, penalty_tiyin=outcome.penalty_tiyin
    )
    if actor in ("admin", "doctor", "clinic_admin"):
        record_audit(
            outbox,
            session,
            Actor(actor if actor == "admin" else "user", actor_id, actor),
            action="booking.cancel",
            object_type="booking",
            object_id=b.id,
            before={"status": previous},
            after={"status": CANCELLED, "refund_tiyin": outcome.refund_tiyin, "reason": reason_text},
            ip=ip,
        )
    TRANSITIONS.labels(to=CANCELLED).inc()
    return b


def cancellation_preview(b: Booking, *, actor: str, platform: dict[str, Any]) -> policy.CancellationOutcome:
    status = CONFIRMED if b.status == IN_PROGRESS else b.status
    return policy.evaluate_cancellation(
        status=status,
        start_at=b.start_at,
        paid_tiyin=paid_net(b),
        actor=actor,
        now=datetime.now(UTC),
        policy=platform["cancellation"],
    )


async def start(session: AsyncSession, b: Booking) -> Booking:
    if b.status == IN_PROGRESS:
        return b
    if b.status != CONFIRMED:
        raise InvalidTransition("Faqat tasdiqlangan qabulni boshlash mumkin")
    now = datetime.now(UTC)
    if now < b.start_at - timedelta(minutes=get_settings().start_window_minutes):
        raise InvalidTransition("Qabulni hali boshlab bo'lmaydi", code="TOO_EARLY")
    b.status = IN_PROGRESS
    b.started_at = now
    payloads.started(session, b)
    TRANSITIONS.labels(to=IN_PROGRESS).inc()
    return b


def _require_finishable(b: Booking, now: datetime) -> None:
    if b.status not in (CONFIRMED, IN_PROGRESS):
        raise InvalidTransition("Faqat tasdiqlangan qabulni yakunlash mumkin")
    if b.start_at > now:
        raise InvalidTransition("Qabul hali boshlanmagan", code="NOT_STARTED")


async def complete(
    session: AsyncSession, b: Booking, *, by: str, note: str = "", actor_id: uuid.UUID | None = None
) -> Booking:
    if b.status in (COMPLETED, NO_SHOW):
        return b
    now = datetime.now(UTC)
    _require_finishable(b, now)
    b.status = COMPLETED
    b.completed_at = now
    b.completed_by = by
    if note:
        b.note = note
    payloads.completed(session, b, await repo.items_of(session, b.id))
    if by != "system":
        record_audit(
            outbox,
            session,
            Actor("admin" if by == "admin" else "user", actor_id, by),
            action="booking.complete",
            object_type="booking",
            object_id=b.id,
            after={"status": COMPLETED},
        )
    TRANSITIONS.labels(to=COMPLETED).inc()
    return b


async def mark_no_show(
    session: AsyncSession, b: Booking, *, absent: str, by: str, note: str = "", actor_id: uuid.UUID | None = None
) -> Booking:
    """`absent=doctor` (faqat admin) — mijozga to'liq qaytariladi (refund — payment, 4-bosqich)."""
    if b.status in (COMPLETED, NO_SHOW):
        return b
    now = datetime.now(UTC)
    _require_finishable(b, now)
    b.status = NO_SHOW
    b.no_show_by = absent
    b.completed_at = now
    b.completed_by = by
    if note:
        b.note = note
    refund = paid_net(b) if absent == "doctor" else 0
    payloads.no_show(session, b, refund_tiyin=refund)
    record_audit(
        outbox,
        session,
        Actor("admin" if by == "admin" else "user", actor_id, by),
        action="booking.no_show",
        object_type="booking",
        object_id=b.id,
        after={"absent": absent, "refund_tiyin": refund},
    )
    TRANSITIONS.labels(to=NO_SHOW).inc()
    return b


async def auto_complete(session: AsyncSession, *, limit: int = 500) -> int:
    before = datetime.now(UTC) - timedelta(hours=get_settings().auto_complete_after_hours)
    rows = await repo.due_auto_complete(session, before, limit)
    for b in rows:
        await complete(session, b, by="system")
    await session.commit()
    return len(rows)


# --- to'lov eventlari ------------------------------------------------------------------------


async def on_payment_captured(session: AsyncSession, deps: Deps, data: dict[str, Any]) -> None:
    """`payment.captured`: slot `confirm` (sinxron) → confirmed. Slot yo'qolgan yoki bron allaqachon yopilgan —
    `booking.confirmation_failed` (pul yechilgan, payment-service qaytaradi). Sinxron chaqiruv xatosi
    (schedule ishlamayapti) — handler yiqiladi va consumer retry qiladi."""
    b = await repo.get(session, uuid.UUID(data["booking_id"]), for_update=True)
    if b is None:
        logger.error("payment for unknown booking", extra={"booking_id": data["booking_id"]})
        return
    amount = int(data["amount_tiyin"])
    payment_id = uuid.UUID(data["payment_id"])
    if b.status in (CONFIRMED, IN_PROGRESS, COMPLETED) and b.payment_id == payment_id:
        return  # takror
    if b.status != PENDING_PAYMENT:
        reason = "booking_cancelled" if b.status == CANCELLED else "booking_expired"
        payloads.confirmation_failed(session, b, payment_id=payment_id, amount_tiyin=amount, reason=reason)
        CONFIRMATION_FAILED.labels(reason=reason).inc()
        return
    try:
        await deps.schedule.confirm(b.slot_id, b.id)
    except UpstreamRejected:
        b.paid_tiyin += amount
        b.payment_id = payment_id
        await expire(session, b, datetime.now(UTC))
        payloads.confirmation_failed(session, b, payment_id=payment_id, amount_tiyin=amount, reason="slot_unavailable")
        CONFIRMATION_FAILED.labels(reason="slot_unavailable").inc()
        return
    b.paid_tiyin += amount
    b.payment_id = payment_id
    b.status = CONFIRMED
    b.confirmed_at = datetime.now(UTC)
    payloads.confirmed(session, b)
    TRANSITIONS.labels(to=CONFIRMED).inc()


async def on_payment_reversed(session: AsyncSession, deps: Deps, data: dict[str, Any]) -> None:
    """Provayder to'lovni bekor qildi (Payme CancelTransaction) — bron tizim tomonidan yopiladi, refund YO'Q
    (pul allaqachon qaytgan)."""
    b = await repo.get(session, uuid.UUID(data["booking_id"]), for_update=True)
    if b is None or b.status in FINAL_STATUSES:
        return
    b.refunded_tiyin += int(data["amount_tiyin"])
    platform = await deps.settings.get()
    await cancel(
        session,
        b,
        actor="system",
        reason_code="payment_reversed",
        reason_text="To'lov qaytarildi",
        platform=platform,
        force_full_refund=False,
    )


async def on_refund_succeeded(session: AsyncSession, data: dict[str, Any]) -> None:
    b = await repo.get(session, uuid.UUID(data["booking_id"]), for_update=True)
    if b is not None:
        b.refunded_tiyin += int(data["amount_tiyin"])


async def cancel_future(
    session: AsyncSession,
    deps: Deps,
    *,
    reason_code: str,
    reason_text: str,
    doctor_id: uuid.UUID | None = None,
    clinic_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
) -> int:
    """Shifokor/klinika to'xtatildi, hamkorlik tugadi, mijoz o'chirildi — kelajakdagi jonli bronlar tizim
    tomonidan bekor qilinadi, 100% qaytariladi (ARCHITECTURE 6.9, 6.10)."""
    platform = await deps.settings.get()
    rows = await repo.future_live(
        session, datetime.now(UTC), doctor_id=doctor_id, clinic_id=clinic_id, client_id=client_id
    )
    for b in rows:
        await cancel(
            session,
            b,
            actor="system",
            reason_code=reason_code,
            reason_text=reason_text,
            platform=platform,
            force_full_refund=True,
        )
    return len(rows)


async def cancel_affiliation(session: AsyncSession, deps: Deps, *, doctor_id: uuid.UUID, clinic_id: uuid.UUID) -> int:
    platform = await deps.settings.get()
    rows = [
        b for b in await repo.future_live(session, datetime.now(UTC), doctor_id=doctor_id) if b.clinic_id == clinic_id
    ]
    for b in rows:
        await cancel(
            session,
            b,
            actor="system",
            reason_code="affiliation_ended",
            reason_text="Shifokor klinika bilan ishlamaydi",
            platform=platform,
            force_full_refund=True,
        )
    return len(rows)


# --- ko'chirish ------------------------------------------------------------------------------


async def reschedule(
    session: AsyncSession, deps: Deps, booking_id: uuid.UUID, client_id: uuid.UUID, new_slot_id: uuid.UUID
) -> Booking:
    """C3: ≤ 2 marta, eski qabulgacha ≥ `full_refund_hours` (bekor qilish pulli, ko'chirish bepul — teshik yopiq),
    yangi slot: shu shifokor, shu joy turi, lead time, davomiylik sig'adi. Narx QAYTA hisoblanmaydi."""
    settings = get_settings()
    platform = await deps.settings.get()
    b = await repo.get(session, booking_id)
    if b is None or b.client_id != client_id:
        raise BookingNotFound()
    if b.status not in (PENDING_PAYMENT, CONFIRMED):
        raise InvalidTransition("Bu bronni ko'chirib bo'lmaydi")
    if b.rescheduled_count >= settings.max_reschedules:
        raise InvalidTransition("Bronni ko'pi bilan 2 marta ko'chirish mumkin", code="RESCHEDULE_LIMIT")
    if new_slot_id == b.slot_id:
        raise InvalidBookingRequest("Yangi vaqt eskisi bilan bir xil")
    now = datetime.now(UTC)
    if b.start_at - now < timedelta(hours=int(platform["cancellation"]["full_refund_hours"])):
        raise InvalidTransition("Qabulga kam vaqt qoldi — ko'chirib bo'lmaydi", code="TOO_LATE")
    slot = await deps.schedule.slot(new_slot_id)
    if slot is None or slot.doctor_id != b.doctor_id:
        raise InvalidBookingRequest("slot_id noto'g'ri")
    if slot.place != b.place:
        raise InvalidBookingRequest("Yangi vaqt joy turi (klinika/uy) bilan mos emas")
    if slot.start_at < now + timedelta(minutes=int(platform["schedule"]["min_lead_time_minutes"])):
        raise InvalidBookingRequest("Bu vaqt juda yaqin")
    items = await repo.items_of(session, b.id)
    if sum(i.duration_minutes for i in items) > slot.duration_minutes:
        raise InvalidBookingRequest("Xizmatlar yangi vaqtga sig'maydi")
    if await repo.patient_overlap(session, b.patient_id, slot.start_at, slot.end_at, now, exclude=b.id):
        raise InvalidBookingRequest("Bemorning shu vaqtda boshqa broni bor", code="PATIENT_BUSY", status_code=409)

    buffer = None
    if b.place == "home":
        doctor = await deps.catalog.bookability(b.doctor_id)
        km = policy.distance_km((doctor or {}).get("home_base"), b.address or {}) if b.address else None
        buffer = policy.travel_buffer_minutes(km)
    try:
        held = await deps.schedule.hold(
            new_slot_id,
            hold_ref=b.id,
            doctor_id=b.doctor_id,
            ttl_seconds=int(platform["schedule"]["hold_ttl_minutes"]) * 60,
            travel_buffer_minutes=buffer,
        )
    except UpstreamRejected as exc:
        raise SlotTaken() from exc
    try:
        if b.status == CONFIRMED:
            held = await deps.schedule.confirm(new_slot_id, b.id)
        locked = await repo.get(session, b.id, for_update=True)
        assert locked is not None
        if locked.status != b.status:
            raise InvalidTransition("Bron holati o'zgardi — qayta urinib ko'ring")
        await expire_stale_on_slot(session, new_slot_id, now)
        old_slot, old_start = locked.slot_id, locked.start_at
        locked.slot_id, locked.start_at, locked.end_at = held.id, held.start_at, held.end_at
        locked.rescheduled_count += 1
        if locked.status == PENDING_PAYMENT:
            locked.payment_due_at = held.hold_expires_at
        payloads.rescheduled(session, locked, old_slot_id=old_slot, old_start_at=old_start, by="client")
        await session.commit()
    except BaseException:
        await session.rollback()
        try:
            await deps.schedule.release(new_slot_id, b.id)
        except Exception:
            logger.warning("compensation: new slot release failed", extra={"slot_id": str(new_slot_id)})
        raise
    TRANSITIONS.labels(to="rescheduled").inc()
    return locked


async def extend_payment(session: AsyncSession, deps: Deps, booking_id: uuid.UUID) -> Booking:
    """payment-service tranzaksiya ochganda (B3): hold va `payment_due_at` uzaytiriladi (faqat uzaytirish)."""
    b = await repo.get(session, booking_id, for_update=True)
    if b is None:
        raise BookingNotFound()
    now = datetime.now(UTC)
    if b.status != PENDING_PAYMENT or b.payment_due_at is None or b.payment_due_at <= now:
        raise InvalidTransition("Bronni to'lab bo'lmaydi", code="NOT_PAYABLE")
    try:
        slot = await deps.schedule.extend(b.slot_id, b.id, get_settings().payment_extension_minutes)
    except UpstreamRejected as exc:
        raise InvalidTransition("Bronni to'lab bo'lmaydi", code="NOT_PAYABLE") from exc
    if slot.hold_expires_at and slot.hold_expires_at > b.payment_due_at:
        b.payment_due_at = slot.hold_expires_at
    await session.commit()
    return b


# --- slot.released / waitlist ----------------------------------------------------------------


async def on_slot_released(session: AsyncSession, data: dict[str, Any]) -> None:
    """Hold muddati o'tgan (schedule job) — mos pending bron yopiladi; bo'shagan vaqt navbatdagiga taklif qilinadi."""
    now = datetime.now(UTC)
    if data["reason"] == "hold_expired" and data.get("hold_ref"):
        try:
            b = await repo.get(session, uuid.UUID(data["hold_ref"]), for_update=True)
        except ValueError:
            b = None
        if b is not None and b.slot_id == uuid.UUID(data["slot_id"]):
            await expire(session, b, now)
    await offer_freed_slot(session, data, now)


async def offer_freed_slot(session: AsyncSession, data: dict[str, Any], now: datetime) -> None:
    settings = get_settings()
    start_at = datetime.fromisoformat(data["start_at"])
    if start_at - now < timedelta(hours=2):  # monolit MIN_LEAD_FOR_NOTICE
        return
    entry = await repo.waitlist_candidate(
        session,
        uuid.UUID(data["doctor_id"]),
        data["place"],
        start_at.astimezone(policy.LOCAL_TZ).date(),
        now - timedelta(minutes=settings.waitlist_notify_cooldown_minutes),
        settings.waitlist_max_notifications,
    )
    if entry is None:
        return
    entry.status = "notified"
    entry.notified_at = now
    entry.notified_count += 1
    outbox.add_event(
        session,
        Events.WAITLIST_SLOT_OFFERED,
        aggregate_id=entry.id,
        data={
            "entry_id": entry.id,
            "client_id": entry.client_id,
            "doctor_id": entry.doctor_id,
            "slot_id": data["slot_id"],
            "start_at": start_at,
            "offer_expires_at": now + timedelta(minutes=settings.waitlist_notify_cooldown_minutes),
        },
    )
