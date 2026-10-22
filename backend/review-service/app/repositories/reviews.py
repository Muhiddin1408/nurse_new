"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Review, ReviewableBooking
from app.models.review import PENDING, PUBLISHED


async def _lock(session: AsyncSession, stmt: Any) -> Any:
    # autoflush=False: qulf ostida qayta o'qishdan oldin flush — aks holda populate_existing
    # flush qilinmagan o'zgarishni bazadagi eski qiymat bilan almashtiradi
    await session.flush()
    return stmt.with_for_update().execution_options(populate_existing=True)


async def upsert_reviewable(session: AsyncSession, row: dict[str, Any]) -> None:
    stmt = insert(ReviewableBooking).values(**row)
    await session.execute(stmt.on_conflict_do_nothing(index_elements=["booking_id"]))


async def reviewable(session: AsyncSession, booking_id: uuid.UUID) -> ReviewableBooking | None:
    return await session.get(ReviewableBooking, booking_id)


async def get(session: AsyncSession, review_id: uuid.UUID, *, for_update: bool = False) -> Review | None:
    stmt = select(Review).where(Review.id == review_id)
    if for_update:
        stmt = await _lock(session, stmt)
    return (await session.scalars(stmt)).one_or_none()


async def by_booking(session: AsyncSession, booking_id: uuid.UUID) -> Review | None:
    return (await session.scalars(select(Review).where(Review.booking_id == booking_id))).one_or_none()


async def fill_clinic(session: AsyncSession, booking_id: uuid.UUID, clinic_id: uuid.UUID) -> Review | None:
    """Sharh proyeksiyadan oldin (booking fallback bilan) yozilgan bo'lsa — klinika keyin to'ldiriladi."""
    stmt = (
        update(Review)
        .where(Review.booking_id == booking_id, Review.clinic_id.is_(None))
        .values(clinic_id=clinic_id)
        .returning(Review)
        .execution_options(synchronize_session=False)
    )
    return (await session.scalars(stmt)).one_or_none()


async def published(
    session: AsyncSession,
    *,
    doctor_id: uuid.UUID | None = None,
    clinic_id: uuid.UUID | None = None,
    limit: int,
    offset: int,
) -> list[Review]:
    stmt = select(Review).where(Review.status == PUBLISHED)
    if doctor_id is not None:
        stmt = stmt.where(Review.doctor_id == doctor_id)
    if clinic_id is not None:
        stmt = stmt.where(Review.clinic_id == clinic_id)
    stmt = stmt.order_by(Review.created_at.desc(), Review.id).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def of_client(session: AsyncSession, client_id: uuid.UUID, *, limit: int) -> list[Review]:
    stmt = select(Review).where(Review.client_id == client_id).order_by(Review.created_at.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())


async def by_status(session: AsyncSession, status: str | None, *, limit: int, offset: int) -> list[Review]:
    stmt = select(Review)
    if status:
        stmt = stmt.where(Review.status == status)
    # Moderatsiya navbati — eskisi birinchi; qolganlari — yangisi birinchi
    order = Review.created_at.asc() if status == PENDING else Review.created_at.desc()
    stmt = stmt.order_by(order, Review.id).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def aggregate(
    session: AsyncSession, *, doctor_id: uuid.UUID | None = None, clinic_id: uuid.UUID | None = None
) -> tuple[float | None, int]:
    """(o'rtacha, soni) — faqat published."""
    stmt = select(func.avg(Review.rating), func.count(Review.id)).where(Review.status == PUBLISHED)
    if doctor_id is not None:
        stmt = stmt.where(Review.doctor_id == doctor_id)
    if clinic_id is not None:
        stmt = stmt.where(Review.clinic_id == clinic_id)
    avg, count = (await session.execute(stmt)).one()
    return (float(avg) if avg is not None else None), int(count)
