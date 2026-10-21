"""Eventlardan proyeksiyalar: xizmatlar keshi (`service.*`), mijoz statistikasi va band qilishlar (`booking.*`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope
from medbron_common.events import Events
from medbron_common.settings_client import CACHE_KEY

from app.repositories import pricing as repo
from app.services import quotes

STATUS_BY_EVENT = {
    Events.BOOKING_CONFIRMED: "confirmed",
    Events.BOOKING_CANCELLED: "cancelled",
    Events.BOOKING_EXPIRED: "expired",
    Events.BOOKING_COMPLETED: "completed",
    Events.BOOKING_NO_SHOW: "no_show",
}


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def on_service(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    values: dict[str, Any] = {
        "service_id": uuid.UUID(data["service_id"]),
        "doctor_id": uuid.UUID(data["doctor_id"]) if data.get("doctor_id") else None,
        "clinic_id": uuid.UUID(data["clinic_id"]) if data.get("clinic_id") else None,
        "specialization_id": uuid.UUID(data["specialization_id"]),
        "name": data["name"],
        "name_ru": data["name_ru"],
        "place": data["place"],
        "duration_minutes": int(data["duration_minutes"]),
        "price_tiyin": int(data["price_tiyin"]),
        "is_active": bool(data["is_active"]),
        "changed_at": event.occurred_at,
    }
    await repo.upsert_service(session, values, keep_fiscal=True)


async def on_service_deactivated(session: AsyncSession, event: Envelope) -> None:
    await repo.deactivate_service(session, uuid.UUID(event.data["service_id"]), event.occurred_at)


async def on_booking(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    booking_id = uuid.UUID(data["booking_id"])
    status = data["status"] if event.event_type == Events.BOOKING_CREATED else STATUS_BY_EVENT[event.event_type]
    values: dict[str, Any] = {
        "booking_id": booking_id,
        "client_id": uuid.UUID(data["client_id"]),
        "doctor_id": uuid.UUID(data["doctor_id"]),
        "status": status,
        "no_show_by": data.get("no_show_by", "") if event.event_type == Events.BOOKING_NO_SHOW else "",
        "completed_at": _iso(data["completed_at"]) if event.event_type == Events.BOOKING_COMPLETED else None,
        "changed_at": event.occurred_at,
    }
    await repo.apply_booking(session, values)
    if event.event_type in (Events.BOOKING_CANCELLED, Events.BOOKING_EXPIRED):
        await quotes.release(session, booking_id)
    else:
        # created (va kechikkan created o'rniga confirmed/completed) — band qilish faollashadi
        await repo.activate_reservations(session, booking_id)


async def on_user_deletion(session: AsyncSession, event: Envelope) -> None:
    await repo.forget_client(session, uuid.UUID(event.data["user_id"]))


async def invalidate_settings(redis: Redis) -> None:
    await redis.delete(CACHE_KEY)
