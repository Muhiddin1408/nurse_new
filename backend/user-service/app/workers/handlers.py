"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi
BITTA tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart
(`scripts/check-contracts.py`).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.repositories import users as repo
from app.services import projections


def register(consumer: EventConsumer) -> None:
    @consumer.on(Events.USER_REGISTERED)
    async def on_user_registered(session: AsyncSession, event: Envelope) -> None:
        await repo.ensure_profile(session, UUID(event.data["user_id"]))

    async def on_booking(session: AsyncSession, event: Envelope) -> None:
        await projections.apply_booking_event(session, event)

    consumer.on(Events.BOOKING_CREATED)(on_booking)
    consumer.on(Events.BOOKING_CONFIRMED)(on_booking)
    consumer.on(Events.BOOKING_CANCELLED)(on_booking)
    consumer.on(Events.BOOKING_EXPIRED)(on_booking)
    consumer.on(Events.BOOKING_COMPLETED)(on_booking)
    consumer.on(Events.BOOKING_NO_SHOW)(on_booking)
