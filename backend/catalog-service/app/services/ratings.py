"""`rating.recalculated` (review-service) → shifokor va klinika reytingi (denormalizatsiya).

Faqat yangiroq event qo'llanadi (`rating_updated_at`) — retry tufayli kechikkan eski hisob yangisini buzmaydi.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope

from app.models import Clinic, Doctor


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 2)))


async def apply(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    at: datetime = event.occurred_at
    await session.execute(
        update(Doctor)
        .where(
            Doctor.id == uuid.UUID(data["doctor_id"]),
            or_(Doctor.rating_updated_at.is_(None), Doctor.rating_updated_at < at),
        )
        .values(rating=_dec(data["rating"]), reviews_count=data["reviews_count"], rating_updated_at=at)
    )
    if data.get("clinic_id"):
        await session.execute(
            update(Clinic)
            .where(
                Clinic.id == uuid.UUID(data["clinic_id"]),
                or_(Clinic.rating_updated_at.is_(None), Clinic.rating_updated_at < at),
            )
            .values(
                rating=_dec(data.get("clinic_rating")),
                reviews_count=data.get("clinic_reviews_count") or 0,
                rating_updated_at=at,
            )
        )
