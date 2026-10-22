"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi BITTA
tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang. Tarmoq chaqiruvi yo'q — refundlar navbatga.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.services import inbound
from app.services.deps import Deps


def register(consumer: EventConsumer, deps: Deps) -> None:
    @consumer.on(Events.BOOKING_CANCELLED)
    async def on_booking_cancelled(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_booking_cancelled(session, event.data)

    @consumer.on(Events.BOOKING_NO_SHOW)
    async def on_booking_no_show(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_booking_no_show(session, event.data)

    @consumer.on(Events.BOOKING_CONFIRMATION_FAILED)
    async def on_confirmation_failed(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_confirmation_failed(session, event.data)

    @consumer.on(Events.DISPUTE_RESOLVED)
    async def on_dispute_resolved(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_dispute_resolved(session, event.data)

    @consumer.on(Events.PAYOUT_PAID)
    async def on_payout_paid(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_payout_paid(session, event.data)

    @consumer.on(Events.ADMIN_SETTINGS_CHANGED)
    async def on_settings_changed(session: AsyncSession, event: Envelope) -> None:
        await deps.settings.invalidate()
