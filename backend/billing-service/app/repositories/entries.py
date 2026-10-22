"""Daromad daftari so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EarningEntry
from app.models.billing import EARNING


async def insert_entry(session: AsyncSession, **values: Any) -> bool:
    """`False` — shu (kind, source_ref) allaqachon yozilgan (takroriy event)."""
    stmt = (
        insert(EarningEntry)
        .values(id=uuid.uuid4(), **values)
        .on_conflict_do_nothing(constraint="uq_earning_entries_kind_source")
        .returning(EarningEntry.id)
    )
    return (await session.execute(stmt)).first() is not None


async def earning_of(session: AsyncSession, booking_id: uuid.UUID) -> EarningEntry | None:
    stmt = select(EarningEntry).where(EarningEntry.booking_id == booking_id, EarningEntry.kind == EARNING)
    return (await session.scalars(stmt)).one_or_none()


async def link_orphans(session: AsyncSession, earning: EarningEntry) -> int:
    """Earning'dan OLDIN kelgan refund/reversal yozuvlariga bron ma'lumotini to'ldiradi."""
    stmt = (
        update(EarningEntry)
        .where(EarningEntry.booking_id == earning.booking_id, EarningEntry.doctor_id.is_(None))
        .values(
            doctor_id=earning.doctor_id,
            clinic_id=earning.clinic_id,
            booking_number=earning.booking_number,
            booking_status=earning.booking_status,
            start_at=earning.start_at,
        )
    )
    return int((await session.execute(stmt)).rowcount or 0)  # type: ignore[attr-defined]


async def doctors_with_unpaid(session: AsyncSession, cutoff: datetime) -> list[uuid.UUID]:
    stmt = (
        select(EarningEntry.doctor_id)
        .where(EarningEntry.payout_id.is_(None), EarningEntry.doctor_id.is_not(None), EarningEntry.eligible_at < cutoff)
        .distinct()
    )
    return [d for d in (await session.scalars(stmt)).all() if d is not None]


async def lock_unpaid(session: AsyncSession, doctor_id: uuid.UUID, cutoff: datetime) -> list[EarningEntry]:
    """SKIP LOCKED — parallel build (deploy paytida ikki scheduler) bir yozuvni ikki payout'ga qo'shmaydi."""
    stmt = (
        select(EarningEntry)
        .where(EarningEntry.doctor_id == doctor_id, EarningEntry.payout_id.is_(None), EarningEntry.eligible_at < cutoff)
        .order_by(EarningEntry.eligible_at, EarningEntry.id)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def assign(session: AsyncSession, entry_ids: Sequence[uuid.UUID], payout_id: uuid.UUID) -> None:
    await session.execute(
        update(EarningEntry)
        .where(EarningEntry.id.in_(entry_ids), EarningEntry.payout_id.is_(None))
        .values(payout_id=payout_id)
    )


async def for_period(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    doctor_id: uuid.UUID | None = None,
    clinic_id: uuid.UUID | None = None,
) -> list[EarningEntry]:
    """Qabul vaqti [start, end) dagi bronlarning BARCHA yozuvlari (refund'lar ham)."""
    bookings = select(EarningEntry.booking_id).where(
        EarningEntry.kind == EARNING, EarningEntry.start_at >= start, EarningEntry.start_at < end
    )
    if doctor_id is not None:
        bookings = bookings.where(EarningEntry.doctor_id == doctor_id)
    if clinic_id is not None:
        bookings = bookings.where(EarningEntry.clinic_id == clinic_id)
    stmt = (
        select(EarningEntry)
        .where(EarningEntry.booking_id.in_(bookings))
        .order_by(EarningEntry.start_at, EarningEntry.booking_id, EarningEntry.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def of_payout(session: AsyncSession, payout_id: uuid.UUID) -> list[EarningEntry]:
    stmt = (
        select(EarningEntry)
        .where(EarningEntry.payout_id == payout_id)
        .order_by(EarningEntry.start_at, EarningEntry.booking_id, EarningEntry.created_at)
    )
    return list((await session.scalars(stmt)).all())
