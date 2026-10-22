"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi BITTA
tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.services import disputes
from app.services.deps import Deps


def register(consumer: EventConsumer, deps: Deps) -> None:
    @consumer.on(Events.REFUND_SUCCEEDED)
    async def on_refund_succeeded(session: AsyncSession, event: Envelope) -> None:
        # Faqat source=dispute (nizo bo'yicha refund) — nizo yopiladi
        await disputes.on_refund_succeeded(session, event.data)

    @consumer.on(Events.ADMIN_SETTINGS_CHANGED)
    async def on_settings_changed(session: AsyncSession, event: Envelope) -> None:
        await deps.settings.invalidate()
