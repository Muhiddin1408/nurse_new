"""Reyting — faqat `published` sharhlardan; `min_reviews_for_rating` dan kam bo'lsa `rating=null` (1 ta 5 ballik
sharh — 5.0 reyting emas). Natija `rating.recalculated` bilan catalog'ga (u denormalizatsiya qiladi, eskisini
`occurred_at` bo'yicha tashlaydi).

Bitta shifokor bo'yicha parallel ikki o'zgarish eskirgan agregat yubormasligi uchun — advisory lock
(tranzaksiya oxirigacha): ikkinchi tranzaksiya birinchisi commit bo'lgach hisoblaydi.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.repositories import reviews as repo
from app.services import payloads


def shown(avg: float | None, count: int) -> float | None:
    if avg is None or count < get_settings().min_reviews_for_rating:
        return None
    return float(Decimal(str(avg)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


async def _lock(session: AsyncSession, key: uuid.UUID) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"rating:{key}", 0))))


async def recalculate(session: AsyncSession, *, doctor_id: uuid.UUID, clinic_id: uuid.UUID | None) -> None:
    """Chaqiruvchining tranzaksiyasi ichida, sharh holati o'zgargandan KEYIN."""
    await session.flush()
    await _lock(session, doctor_id)
    avg, count = await repo.aggregate(session, doctor_id=doctor_id)
    clinic_rating: float | None = None
    clinic_count: int | None = None
    if clinic_id is not None:
        await _lock(session, clinic_id)
        c_avg, clinic_count = await repo.aggregate(session, clinic_id=clinic_id)
        clinic_rating = shown(c_avg, clinic_count)
    payloads.rating_recalculated(
        session,
        doctor_id=doctor_id,
        rating=shown(avg, count),
        reviews_count=count,
        clinic_id=clinic_id,
        clinic_rating=clinic_rating,
        clinic_reviews_count=clinic_count,
    )
