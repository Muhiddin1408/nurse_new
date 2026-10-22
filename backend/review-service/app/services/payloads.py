"""Chiquvchi eventlar (contracts/events/*.json). Har biri biznes yozuvi bilan BITTA tranzaksiyada outbox'ga."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.events import Events

from app.events import outbox
from app.models import Review


def submitted(session: AsyncSession, r: Review) -> None:
    outbox.add_event(
        session,
        Events.REVIEW_SUBMITTED,
        aggregate_id=r.id,
        data={
            "review_id": r.id,
            "booking_id": r.booking_id,
            "doctor_id": r.doctor_id,
            "client_id": r.client_id,
            "rating": r.rating,
            "flags": r.moderation_flags,
            "status": r.status,
        },
    )


def published(session: AsyncSession, r: Review) -> None:
    # doctor_user_id — bizda yo'q (notification doctor_id → user proyeksiyasidan topadi)
    outbox.add_event(
        session,
        Events.REVIEW_PUBLISHED,
        aggregate_id=r.id,
        data={"review_id": r.id, "booking_id": r.booking_id, "doctor_id": r.doctor_id, "doctor_user_id": None,
              "rating": r.rating},
    )  # fmt: skip


def rejected(session: AsyncSession, r: Review) -> None:
    outbox.add_event(
        session,
        Events.REVIEW_REJECTED,
        aggregate_id=r.id,
        data={"review_id": r.id, "booking_id": r.booking_id, "client_id": r.client_id, "reason": r.reject_reason},
    )


def rating_recalculated(
    session: AsyncSession,
    *,
    doctor_id: uuid.UUID,
    rating: float | None,
    reviews_count: int,
    clinic_id: uuid.UUID | None,
    clinic_rating: float | None,
    clinic_reviews_count: int | None,
) -> None:
    outbox.add_event(
        session,
        Events.RATING_RECALCULATED,
        aggregate_id=doctor_id,
        data={
            "doctor_id": doctor_id,
            "rating": rating,
            "reviews_count": reviews_count,
            "clinic_id": clinic_id,
            "clinic_rating": clinic_rating,
            "clinic_reviews_count": clinic_reviews_count,
        },
    )
