"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi BITTA
tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.services import reviews
from app.services.deps import Deps


def register(consumer: EventConsumer, deps: Deps) -> None:
    @consumer.on(Events.BOOKING_COMPLETED)
    async def on_booking_completed(session: AsyncSession, event: Envelope) -> None:
        await reviews.on_booking_completed(session, event.data)

    @consumer.on(Events.ADMIN_SETTINGS_CHANGED)
    async def on_settings_changed(session: AsyncSession, event: Envelope) -> None:
        await deps.settings.invalidate()
