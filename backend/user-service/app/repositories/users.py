"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Address, BookingState, Favorite, Patient, Profile

ACTIVE_BOOKING_STATUSES = frozenset({"pending_payment", "confirmed", "in_progress"})
TERMINAL_BOOKING_STATUSES = frozenset({"cancelled", "expired", "completed", "no_show"})


async def ensure_profile(session: AsyncSession, user_id: uuid.UUID) -> Profile:
    """Profil `user.registered` eventidan yaratiladi; event hali yetib kelmagan bo'lsa — birinchi
    murojaatda (ikkalasi ham ON CONFLICT DO NOTHING, natija bir xil)."""
    await session.execute(
        insert(Profile).values(user_id=user_id).on_conflict_do_nothing(index_elements=[Profile.user_id])
    )
    profile = (await session.scalars(select(Profile).where(Profile.user_id == user_id).with_for_update())).one()
    return profile


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> Profile | None:
    return await session.get(Profile, user_id)


# --- patients --------------------------------------------------------------------------------


async def list_patients(session: AsyncSession, user_id: uuid.UUID) -> Sequence[Patient]:
    stmt = (
        select(Patient)
        .where(Patient.user_id == user_id, Patient.is_deleted.is_(False))
        .order_by(Patient.created_at.desc())
    )
    return (await session.scalars(stmt)).all()


async def count_patients(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(Patient.user_id == user_id, Patient.is_deleted.is_(False))
    return int((await session.execute(stmt)).scalar_one())


async def get_owned_patient(
    session: AsyncSession, user_id: uuid.UUID, patient_id: uuid.UUID, *, for_update: bool = False
) -> Patient | None:
    """Egalik shu so'rov ichida: begona va yo'q bemor — bir xil `None` (IDOR, monolit A1/A4)."""
    stmt = select(Patient).where(Patient.id == patient_id, Patient.user_id == user_id, Patient.is_deleted.is_(False))
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


# --- addresses -------------------------------------------------------------------------------


async def list_addresses(session: AsyncSession, user_id: uuid.UUID) -> Sequence[Address]:
    stmt = (
        select(Address)
        .where(Address.user_id == user_id, Address.is_deleted.is_(False))
        .order_by(Address.is_default.desc(), Address.created_at.desc())
    )
    return (await session.scalars(stmt)).all()


async def count_addresses(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(Address.user_id == user_id, Address.is_deleted.is_(False))
    return int((await session.execute(stmt)).scalar_one())


async def get_owned_address(
    session: AsyncSession, user_id: uuid.UUID, address_id: uuid.UUID, *, for_update: bool = False
) -> Address | None:
    stmt = select(Address).where(Address.id == address_id, Address.user_id == user_id, Address.is_deleted.is_(False))
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def clear_default_address(session: AsyncSession, user_id: uuid.UUID, keep: uuid.UUID | None) -> None:
    stmt = update(Address).where(Address.user_id == user_id, Address.is_default.is_(True))
    if keep is not None:
        stmt = stmt.where(Address.id != keep)
    await session.execute(stmt.values(is_default=False))


# --- favorites -------------------------------------------------------------------------------


async def list_favorites(session: AsyncSession, user_id: uuid.UUID) -> Sequence[Favorite]:
    stmt = select(Favorite).where(Favorite.user_id == user_id).order_by(Favorite.created_at.desc())
    return (await session.scalars(stmt)).all()


async def count_favorites(session: AsyncSession, user_id: uuid.UUID) -> int:
    return int((await session.execute(select(func.count()).where(Favorite.user_id == user_id))).scalar_one())


async def add_favorite(session: AsyncSession, user_id: uuid.UUID, doctor_id: uuid.UUID) -> None:
    await session.execute(
        insert(Favorite)
        .values(user_id=user_id, doctor_id=doctor_id)
        .on_conflict_do_nothing(index_elements=[Favorite.user_id, Favorite.doctor_id])
    )


async def remove_favorite(session: AsyncSession, user_id: uuid.UUID, doctor_id: uuid.UUID) -> None:
    await session.execute(delete(Favorite).where(Favorite.user_id == user_id, Favorite.doctor_id == doctor_id))


# --- booking projection ----------------------------------------------------------------------


async def apply_booking_state(
    session: AsyncSession,
    *,
    booking_id: uuid.UUID,
    user_id: uuid.UUID,
    patient_id: uuid.UUID,
    status: str,
    changed_at: datetime,
) -> None:
    """Yangiroq event qo'llanadi; yakuniy holat hech qachon qaytarilmaydi (retry/tartibsizlikka chidamli)."""
    stmt = insert(BookingState).values(
        booking_id=booking_id,
        user_id=user_id,
        patient_id=patient_id,
        status=status,
        is_active=status in ACTIVE_BOOKING_STATUSES,
        changed_at=changed_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[BookingState.booking_id],
        set_={"status": status, "is_active": status in ACTIVE_BOOKING_STATUSES, "changed_at": changed_at},
        where=(BookingState.changed_at < changed_at) & BookingState.status.not_in(tuple(TERMINAL_BOOKING_STATUSES)),
    )
    await session.execute(stmt)


async def active_bookings(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(BookingState.user_id == user_id, BookingState.is_active.is_(True))
    return int((await session.execute(stmt)).scalar_one())


async def patient_bookings(session: AsyncSession, patient_id: uuid.UUID) -> tuple[int, int]:
    """(faol, jami) — bemorni o'chirishda: faol bo'lsa 409, tarix bo'lsa yumshoq o'chirish."""
    stmt = select(
        func.count().filter(BookingState.is_active.is_(True)),
        func.count(),
    ).where(BookingState.patient_id == patient_id)
    active, total = (await session.execute(stmt)).one()
    return int(active), int(total)
