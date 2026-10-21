"""Payout va to'lov rekvizitlari so'rovlari."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payout, PayoutAccount


async def create(session: AsyncSession, **values: Any) -> Payout | None:
    """`None` — shu shifokor va davr uchun payout allaqachon bor (qayta build)."""
    stmt = (
        insert(Payout)
        .values(id=uuid.uuid4(), **values)
        .on_conflict_do_nothing(constraint="uq_payouts_doctor_period")
        .returning(Payout)
    )
    return (await session.scalars(stmt)).one_or_none()


async def get(session: AsyncSession, payout_id: uuid.UUID, *, for_update: bool = False) -> Payout | None:
    stmt = select(Payout).where(Payout.id == payout_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def statuses(session: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(Payout.id, Payout.status).where(Payout.id.in_(ids)))
    return {pid: status for pid, status in rows.all()}


async def list_(
    session: AsyncSession,
    *,
    doctor_id: uuid.UUID | None = None,
    status: str | None = None,
    period_end: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Payout]:
    stmt = select(Payout).order_by(Payout.period_end.desc(), Payout.created_at.desc(), Payout.id)
    if doctor_id is not None:
        stmt = stmt.where(Payout.doctor_id == doctor_id)
    if status is not None:
        stmt = stmt.where(Payout.status == status)
    if period_end is not None:
        stmt = stmt.where(Payout.period_end == period_end)
    return list((await session.scalars(stmt.limit(limit).offset(offset))).all())


async def account(session: AsyncSession, doctor_id: uuid.UUID) -> PayoutAccount | None:
    return await session.get(PayoutAccount, doctor_id)


async def upsert_account(session: AsyncSession, doctor_id: uuid.UUID, **values: Any) -> PayoutAccount:
    stmt = (
        insert(PayoutAccount)
        .values(doctor_id=doctor_id, **values)
        .on_conflict_do_update(index_elements=[PayoutAccount.doctor_id], set_={**values, "updated_at": func.now()})
        .returning(PayoutAccount)
        .execution_options(populate_existing=True)
    )
    return (await session.scalars(stmt)).one()
