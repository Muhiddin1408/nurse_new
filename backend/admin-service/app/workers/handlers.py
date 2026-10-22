"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi
BITTA tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart
(`scripts/check-contracts.py`). Navbat: `admin-service.audit` (reestr 5-bo'lim).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.services import audit


def register(consumer: EventConsumer) -> None:
    @consumer.on(Events.AUDIT_RECORDED)
    async def on_audit_recorded(session: AsyncSession, event: Envelope) -> None:
        await audit.record_from_event(session, event)
