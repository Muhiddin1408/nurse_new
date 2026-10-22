"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi
BITTA tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart
(`scripts/check-contracts.py`).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.core.redis import get_redis
from app.services import projections


async def on_settings_changed(session: AsyncSession, event: Envelope) -> None:
    # Global sozlamalar keshi (Redis — API bilan umumiy) tashlanadi; keyingi quote yangisini o'qiydi
    await projections.invalidate_settings(get_redis())


def register(consumer: EventConsumer) -> None:
    consumer.on(Events.ADMIN_SETTINGS_CHANGED)(on_settings_changed)
    consumer.on(Events.USER_DELETION_REQUESTED)(projections.on_user_deletion)
    consumer.on(Events.SERVICE_CREATED)(projections.on_service)
    consumer.on(Events.SERVICE_UPDATED)(projections.on_service)
    consumer.on(Events.SERVICE_DEACTIVATED)(projections.on_service_deactivated)
    consumer.on(Events.BOOKING_CREATED)(projections.on_booking)
    consumer.on(Events.BOOKING_CONFIRMED)(projections.on_booking)
    consumer.on(Events.BOOKING_CANCELLED)(projections.on_booking)
    consumer.on(Events.BOOKING_EXPIRED)(projections.on_booking)
    consumer.on(Events.BOOKING_COMPLETED)(projections.on_booking)
    consumer.on(Events.BOOKING_NO_SHOW)(projections.on_booking)
