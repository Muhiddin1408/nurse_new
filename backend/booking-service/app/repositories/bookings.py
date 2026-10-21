"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Booking, BookingItem, IdempotencyKey, WaitlistEntry
from app.models.booking import CONFIRMED, IN_PROGRESS, LIVE_STATUSES, PENDING_PAYMENT


async def get(session: AsyncSession, booking_id: uuid.UUID, *, for_update: bool = False) -> Booking | None:
    stmt = select(Booking).where(Booking.id == booking_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def items_of(session: AsyncSession, booking_id: uuid.UUID) -> list[BookingItem]:
    stmt = select(BookingItem).where(BookingItem.booking_id == booking_id).order_by(BookingItem.id)
    return list((await session.scalars(stmt)).all())


async def items_for(session: AsyncSession, booking_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[BookingItem]]:
    out: dict[uuid.UUID, list[BookingItem]] = {b: [] for b in booking_ids}
    if booking_ids:
        stmt = select(BookingItem).where(BookingItem.booking_id.in_(booking_ids)).order_by(BookingItem.id)
        for item in (await session.scalars(stmt)).all():
            out[item.booking_id].append(item)
    return out


async def number_exists(session: AsyncSession, number: str) -> bool:
    return (await session.execute(select(Booking.id).where(Booking.number == number))).first() is not None


def _live(now: datetime):  # type: ignore[no-untyped-def]
    """Jonli = tasdiqlangan/boshlangan YOKI to'lov muddati o'tmagan pending (lazy expiry, E8)."""
    return or_(
        Booking.status.in_((CONFIRMED, IN_PROGRESS)),
        and_(Booking.status == PENDING_PAYMENT, Booking.payment_due_at > now),
    )


async def live_pending_count(session: AsyncSession, client_id: uuid.UUID, now: datetime) -> int:
    stmt = select(func.count()).where(
        Booking.client_id == client_id, Booking.status == PENDING_PAYMENT, Booking.payment_due_at > now
    )
    return int((await session.execute(stmt)).scalar_one())


async def patient_overlap(
    session: AsyncSession,
    patient_id: uuid.UUID,
    start: datetime,
    end: datetime,
    now: datetime,
    exclude: uuid.UUID | None = None,
) -> bool:
    stmt = select(Booking.id).where(
        Booking.patient_id == patient_id, Booking.start_at < end, Booking.end_at > start, _live(now)
    )
    if exclude is not None:
        stmt = stmt.where(Booking.id != exclude)
    return (await session.execute(stmt.limit(1))).first() is not None


async def list_for_client(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    statuses: Sequence[str] | None,
    start_from: datetime | None,
    start_to: datetime | None,
    cursor: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> Sequence[Booking]:
    stmt = select(Booking).where(Booking.client_id == client_id)
    if statuses:
        stmt = stmt.where(Booking.status.in_(statuses))
    if start_from:
        stmt = stmt.where(Booking.start_at >= start_from)
    if start_to:
        stmt = stmt.where(Booking.start_at < start_to)
    if cursor:
        stmt = stmt.where(
            or_(Booking.created_at < cursor[0], and_(Booking.created_at == cursor[0], Booking.id < cursor[1]))
        )
    stmt = stmt.order_by(Booking.created_at.desc(), Booking.id.desc()).limit(limit)
    return (await session.scalars(stmt)).all()


async def list_for_doctor(
    session: AsyncSession, doctor_id: uuid.UUID, *, start: datetime, end: datetime, statuses: Sequence[str]
) -> Sequence[Booking]:
    stmt = (
        select(Booking)
        .where(
            Booking.doctor_id == doctor_id,
            Booking.start_at >= start,
            Booking.start_at < end,
            Booking.status.in_(statuses),
        )
        .order_by(Booking.start_at)
    )
    return (await session.scalars(stmt)).all()


async def list_for_clinic(
    session: AsyncSession, clinic_id: uuid.UUID, *, start: datetime, end: datetime, q: str | None
) -> Sequence[Booking]:
    stmt = select(Booking).where(
        Booking.clinic_id == clinic_id, Booking.start_at >= start, Booking.start_at < end, Booking.status != "expired"
    )
    if q:
        stmt = stmt.where(or_(Booking.number.ilike(f"%{q}%"), Booking.patient["full_name"].astext.ilike(f"%{q}%")))
    return (await session.scalars(stmt.order_by(Booking.start_at))).all()


async def patient_history(session: AsyncSession, doctor_id: uuid.UUID, patient_id: uuid.UUID) -> Sequence[Booking]:
    stmt = (
        select(Booking)
        .where(
            Booking.doctor_id == doctor_id,
            Booking.patient_id == patient_id,
            Booking.status.in_(("completed", "no_show")),
        )
        .order_by(Booking.start_at.desc())
        .limit(20)
    )
    return (await session.scalars(stmt)).all()


async def admin_list(
    session: AsyncSession,
    *,
    status: str | None,
    doctor_id: uuid.UUID | None,
    client_id: uuid.UUID | None,
    number: str | None,
    start_from: datetime | None,
    start_to: datetime | None,
    limit: int,
    offset: int,
) -> Sequence[Booking]:
    stmt = select(Booking)
    if status:
        stmt = stmt.where(Booking.status == status)
    if doctor_id:
        stmt = stmt.where(Booking.doctor_id == doctor_id)
    if client_id:
        stmt = stmt.where(Booking.client_id == client_id)
    if number:
        stmt = stmt.where(Booking.number == number.upper())
    if start_from:
        stmt = stmt.where(Booking.start_at >= start_from)
    if start_to:
        stmt = stmt.where(Booking.start_at < start_to)
    return (await session.scalars(stmt.order_by(Booking.created_at.desc()).limit(limit).offset(offset))).all()


async def due_unpaid(session: AsyncSession, now: datetime, limit: int) -> Sequence[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.status == PENDING_PAYMENT, Booking.payment_due_at < now)
        .order_by(Booking.payment_due_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return (await session.scalars(stmt)).all()


async def due_auto_complete(session: AsyncSession, before: datetime, limit: int) -> Sequence[Booking]:
    stmt = (
        select(Booking)
        .where(Booking.status.in_((CONFIRMED, IN_PROGRESS)), Booking.end_at <= before)
        .order_by(Booking.end_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return (await session.scalars(stmt)).all()


async def future_live(
    session: AsyncSession,
    now: datetime,
    *,
    doctor_id: uuid.UUID | None = None,
    clinic_id: uuid.UUID | None = None,
    client_id: uuid.UUID | None = None,
) -> Sequence[Booking]:
    stmt = select(Booking).where(Booking.status.in_(LIVE_STATUSES), Booking.start_at > now)
    if doctor_id:
        stmt = stmt.where(Booking.doctor_id == doctor_id)
    if clinic_id:
        stmt = stmt.where(Booking.clinic_id == clinic_id)
    if client_id:
        stmt = stmt.where(Booking.client_id == client_id)
    return (await session.scalars(stmt.order_by(Booking.start_at).with_for_update())).all()


# --- idempotency -----------------------------------------------------------------------------


async def claim_key(session: AsyncSession, client_id: uuid.UUID, key: str, request_hash: str) -> IdempotencyKey | None:
    """Kalitni egallaydi. Qaytaradi: None — yangi (shu so'rov egasi), aks holda mavjud qator."""
    stmt = (
        insert(IdempotencyKey)
        .values(client_id=client_id, key=key, request_hash=request_hash)
        .on_conflict_do_nothing(index_elements=[IdempotencyKey.client_id, IdempotencyKey.key])
        .returning(IdempotencyKey.key)
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return None
    return await session.get(IdempotencyKey, (client_id, key))


async def finish_key(session: AsyncSession, client_id: uuid.UUID, key: str, booking_id: uuid.UUID) -> None:
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.client_id == client_id, IdempotencyKey.key == key)
        .values(booking_id=booking_id)
    )


async def drop_key(session: AsyncSession, client_id: uuid.UUID, key: str) -> None:
    await session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.client_id == client_id, IdempotencyKey.key == key, IdempotencyKey.booking_id.is_(None)
        )
    )


async def purge_keys(session: AsyncSession, before: datetime) -> int:
    result = await session.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < before))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- waitlist --------------------------------------------------------------------------------


async def waitlist_live(session: AsyncSession, client_id: uuid.UUID, doctor_id: uuid.UUID) -> WaitlistEntry | None:
    stmt = select(WaitlistEntry).where(
        WaitlistEntry.client_id == client_id,
        WaitlistEntry.doctor_id == doctor_id,
        WaitlistEntry.status.in_(("active", "notified")),
    )
    return (await session.scalars(stmt.with_for_update())).one_or_none()


async def waitlist_of(session: AsyncSession, client_id: uuid.UUID) -> Sequence[WaitlistEntry]:
    stmt = (
        select(WaitlistEntry)
        .where(WaitlistEntry.client_id == client_id, WaitlistEntry.status.in_(("active", "notified")))
        .order_by(WaitlistEntry.created_at.desc())
    )
    return (await session.scalars(stmt)).all()


async def waitlist_candidate(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    place: str,
    day: date,
    cooldown_before: datetime,
    max_notifications: int,
) -> WaitlistEntry | None:
    stmt = (
        select(WaitlistEntry)
        .where(
            WaitlistEntry.doctor_id == doctor_id,
            WaitlistEntry.place == place,
            WaitlistEntry.date_from <= day,
            WaitlistEntry.date_to >= day,
            WaitlistEntry.status.in_(("active", "notified")),
            WaitlistEntry.notified_count < max_notifications,
            or_(WaitlistEntry.notified_at.is_(None), WaitlistEntry.notified_at < cooldown_before),
        )
        .order_by(WaitlistEntry.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return (await session.scalars(stmt)).one_or_none()


async def mark_waitlist_converted(session: AsyncSession, client_id: uuid.UUID, doctor_id: uuid.UUID) -> None:
    await session.execute(
        update(WaitlistEntry)
        .where(
            WaitlistEntry.client_id == client_id,
            WaitlistEntry.doctor_id == doctor_id,
            WaitlistEntry.status.in_(("active", "notified")),
        )
        .values(status="converted")
    )


async def expire_waitlist(session: AsyncSession, today: date) -> int:
    result = await session.execute(
        update(WaitlistEntry)
        .where(WaitlistEntry.status.in_(("active", "notified")), WaitlistEntry.date_to < today)
        .values(status="expired")
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
