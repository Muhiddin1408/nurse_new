"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BookingProjection, Contact, DoctorContact, Notification, PushDevice

TERMINAL_BOOKING = ("cancelled", "expired", "completed", "no_show")

# --- contacts --------------------------------------------------------------------------------


async def upsert_contact(session: AsyncSession, user_id: uuid.UUID, **values: Any) -> None:
    stmt = insert(Contact).values(user_id=user_id, **values)
    if values:
        stmt = stmt.on_conflict_do_update(index_elements=[Contact.user_id], set_=values)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=[Contact.user_id])
    await session.execute(stmt)


async def get_contact(session: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False) -> Contact | None:
    stmt = select(Contact).where(Contact.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def contact_by_chat(session: AsyncSession, chat_id: int) -> Contact | None:
    return (await session.scalars(select(Contact).where(Contact.telegram_chat_id == chat_id))).one_or_none()


async def unlink_chat(session: AsyncSession, chat_id: int, except_user: uuid.UUID) -> None:
    await session.execute(
        update(Contact)
        .where(Contact.telegram_chat_id == chat_id, Contact.user_id != except_user)
        .values(telegram_chat_id=None)
    )


async def upsert_doctor(
    session: AsyncSession, *, doctor_id: uuid.UUID, user_id: uuid.UUID, full_name: str | None, changed_at: datetime
) -> None:
    """`full_name=None` — ism o'zgartirilmaydi (masalan, `affiliation.invited` faqat user_id ni biladi)."""
    update_values: dict[str, Any] = {"user_id": user_id, "changed_at": changed_at}
    if full_name is not None:
        update_values["full_name"] = full_name
    stmt = (
        insert(DoctorContact)
        .values(doctor_id=doctor_id, user_id=user_id, full_name=full_name or "", changed_at=changed_at)
        .on_conflict_do_update(
            index_elements=[DoctorContact.doctor_id], set_=update_values, where=DoctorContact.changed_at <= changed_at
        )
    )
    await session.execute(stmt)


async def get_doctor(session: AsyncSession, doctor_id: uuid.UUID) -> DoctorContact | None:
    return await session.get(DoctorContact, doctor_id)


async def doctor_ids_of_user(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.scalars(select(DoctorContact.doctor_id).where(DoctorContact.user_id == user_id))
    return list(rows.all())


# --- devices ---------------------------------------------------------------------------------


async def upsert_device(session: AsyncSession, user_id: uuid.UUID, token: str, platform: str, now: datetime) -> None:
    """Token boshqa akkauntda bo'lsa (bitta telefonda akkaunt almashtirildi) — shu akkauntga o'tadi."""
    stmt = insert(PushDevice).values(id=uuid.uuid4(), user_id=user_id, token=token, platform=platform, last_seen_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PushDevice.token],
        set_={"user_id": user_id, "platform": platform, "is_active": True, "last_seen_at": now},
    )
    await session.execute(stmt)


async def delete_device(session: AsyncSession, user_id: uuid.UUID, token: str) -> None:
    await session.execute(delete(PushDevice).where(PushDevice.user_id == user_id, PushDevice.token == token))


async def active_devices(session: AsyncSession, user_id: uuid.UUID) -> Sequence[PushDevice]:
    stmt = select(PushDevice).where(PushDevice.user_id == user_id, PushDevice.is_active.is_(True))
    return (await session.scalars(stmt)).all()


async def deactivate_device(session: AsyncSession, device_id: uuid.UUID) -> None:
    await session.execute(update(PushDevice).where(PushDevice.id == device_id).values(is_active=False))


async def drop_user_devices(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(delete(PushDevice).where(PushDevice.user_id == user_id))


# --- bookings --------------------------------------------------------------------------------


async def apply_booking(session: AsyncSession, values: dict[str, Any]) -> None:
    """Yangiroq event qo'llanadi; yakuniy holat qaytarilmaydi."""
    stmt = insert(BookingProjection).values(**values)
    update_set = {k: v for k, v in values.items() if k != "booking_id"}
    stmt = stmt.on_conflict_do_update(
        index_elements=[BookingProjection.booking_id],
        set_=update_set,
        where=and_(
            BookingProjection.changed_at < values["changed_at"],
            BookingProjection.status.not_in(TERMINAL_BOOKING),
        ),
    )
    await session.execute(stmt)


async def get_booking(session: AsyncSession, booking_id: uuid.UUID) -> BookingProjection | None:
    return await session.get(BookingProjection, booking_id)


async def doctor_day(
    session: AsyncSession, doctor_id: uuid.UUID, start: datetime, end: datetime
) -> Sequence[BookingProjection]:
    stmt = (
        select(BookingProjection)
        .where(
            BookingProjection.doctor_id == doctor_id,
            BookingProjection.start_at >= start,
            BookingProjection.start_at < end,
            BookingProjection.status.in_(("confirmed", "in_progress", "completed")),
        )
        .order_by(BookingProjection.start_at)
    )
    return (await session.scalars(stmt)).all()


async def doctors_with_bookings(
    session: AsyncSession, start: datetime, end: datetime
) -> list[tuple[uuid.UUID, int, datetime]]:
    stmt = (
        select(BookingProjection.doctor_id, func.count(), func.min(BookingProjection.start_at))
        .where(
            BookingProjection.start_at >= start,
            BookingProjection.start_at < end,
            BookingProjection.status == "confirmed",
        )
        .group_by(BookingProjection.doctor_id)
    )
    return [(row[0], int(row[1]), row[2]) for row in (await session.execute(stmt)).all()]


# --- notifications (outbox) ------------------------------------------------------------------


async def enqueue(session: AsyncSession, values: dict[str, Any]) -> bool:
    """`dedupe_key` bo'yicha idempotent. Qaytaradi: yangi qator yaratildimi."""
    stmt = (
        insert(Notification)
        .values(id=uuid.uuid4(), **values)
        .on_conflict_do_nothing(index_elements=[Notification.dedupe_key])
        .returning(Notification.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def cancel_pending(
    session: AsyncSession, *, booking_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None, reason: str
) -> int:
    stmt = update(Notification).where(Notification.status == "pending")
    if booking_id is not None:
        stmt = stmt.where(Notification.booking_id == booking_id, Notification.kind.is_not(None))
    if user_id is not None:
        stmt = stmt.where(Notification.user_id == user_id)
    result = await session.execute(stmt.values(status="cancelled", last_error=reason))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def delete_unsent_reminders(session: AsyncSession, booking_id: uuid.UUID) -> None:
    """Ko'chirishda: yuborilmagan eslatmalar o'chiriladi va qayta rejalashtiriladi (UNIQUE dedupe_key)."""
    await session.execute(
        delete(Notification).where(
            Notification.booking_id == booking_id,
            Notification.kind.is_not(None),
            Notification.status.in_(("pending", "cancelled", "skipped")),
        )
    )


async def claim_due(session: AsyncSession, now: datetime, lease_until: datetime, limit: int) -> Sequence[Notification]:
    """Vaqti kelgan xabarlarni "ijaraga" oladi: SKIP LOCKED — parallel dispatcher'lar bitta xabarni olmaydi;
    `sending` holatida jarayon o'lsa, `locked_until` o'tgach qayta olinadi."""
    due = (
        select(Notification.id)
        .where(
            Notification.send_at <= now,
            or_(
                Notification.status == "pending",
                and_(Notification.status == "sending", Notification.locked_until < now),
            ),
        )
        .order_by(Notification.send_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    stmt = (
        update(Notification)
        .where(Notification.id.in_(due.scalar_subquery()))
        .values(status="sending", locked_until=lease_until)
        .returning(Notification)
    )
    return (await session.scalars(stmt)).all()


async def list_logs(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    status: str | None,
    template: str | None,
    before: datetime | None,
    limit: int,
) -> Sequence[Notification]:
    stmt = select(Notification)
    if user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    if status:
        stmt = stmt.where(Notification.status == status)
    if template:
        stmt = stmt.where(Notification.template == template)
    if before:
        stmt = stmt.where(Notification.created_at < before)
    return (await session.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit))).all()


async def backlog(session: AsyncSession, now: datetime) -> int:
    stmt = select(func.count()).where(Notification.status == "pending", Notification.send_at <= now)
    return int((await session.execute(stmt)).scalar_one())


async def purge_old(session: AsyncSession, before: datetime) -> int:
    stmt = delete(Notification).where(
        Notification.created_at < before, Notification.status.in_(("sent", "skipped", "cancelled", "failed"))
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
