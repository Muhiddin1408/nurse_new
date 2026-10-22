"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Dispute, Evidence
from app.models.dispute import OPEN


def _locked(stmt: Any) -> Any:
    """FOR UPDATE + identity map'dagi obyektni qulf ostidagi qiymatlar bilan yangilash."""
    return stmt.with_for_update().execution_options(populate_existing=True)


async def get(session: AsyncSession, dispute_id: uuid.UUID, *, for_update: bool = False) -> Dispute | None:
    stmt = select(Dispute).where(Dispute.id == dispute_id)
    if for_update:
        await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni bazadagisi bilan almashtirmasin
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).one_or_none()


async def by_booking(session: AsyncSession, booking_id: uuid.UUID) -> Dispute | None:
    return (await session.scalars(select(Dispute).where(Dispute.booking_id == booking_id))).one_or_none()


async def list_for_client(session: AsyncSession, client_id: uuid.UUID, *, limit: int, offset: int) -> list[Dispute]:
    stmt = (
        select(Dispute)
        .where(Dispute.client_id == client_id)
        .order_by(Dispute.created_at.desc(), Dispute.id)
        .limit(limit)
        .offset(offset)
    )
    return list((await session.scalars(stmt)).all())


async def search(
    session: AsyncSession,
    *,
    status: str | None,
    overdue_at: datetime | None,
    booking_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> list[Dispute]:
    """Admin navbati — SLA bo'yicha (eng shoshilinchi birinchi)."""
    stmt = select(Dispute)
    if status:
        stmt = stmt.where(Dispute.status == status)
    if overdue_at is not None:
        stmt = stmt.where(Dispute.status == OPEN, Dispute.due_at < overdue_at)
    if booking_id is not None:
        stmt = stmt.where(Dispute.booking_id == booking_id)
    stmt = stmt.order_by(Dispute.due_at, Dispute.id).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def sla_due(session: AsyncSession, now: datetime, *, limit: int) -> list[Dispute]:
    stmt = (
        select(Dispute)
        .where(Dispute.status == OPEN, Dispute.sla_breached_at.is_(None), Dispute.due_at < now)
        .order_by(Dispute.due_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def evidence_of(session: AsyncSession, dispute_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Evidence]]:
    out: dict[uuid.UUID, list[Evidence]] = {d: [] for d in dispute_ids}
    if dispute_ids:
        stmt = select(Evidence).where(Evidence.dispute_id.in_(dispute_ids)).order_by(Evidence.id)
        for e in (await session.scalars(stmt)).all():
            out[e.dispute_id].append(e)
    return out


async def evidence_count(session: AsyncSession, dispute_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Evidence).where(Evidence.dispute_id == dispute_id)
    return int((await session.execute(stmt)).scalar_one())


async def add_evidence(
    session: AsyncSession, *, dispute_id: uuid.UUID, file_id: uuid.UUID, added_by: uuid.UUID, content_type: str
) -> None:
    """Idempotent: o'sha fayl ikkinchi marta qo'shilmaydi."""
    stmt = (
        insert(Evidence)
        .values(dispute_id=dispute_id, file_id=file_id, added_by=added_by, content_type=content_type[:64])
        .on_conflict_do_nothing(index_elements=["dispute_id", "file_id"])
    )
    await session.execute(stmt)
