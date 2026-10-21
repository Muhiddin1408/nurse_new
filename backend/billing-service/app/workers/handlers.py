"""Event handlerlar. Har handler: (session, event) — daftar yozuvi va idempotentlik belgisi BITTA
tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang. Daftar yozuvlari o'zi ham idempotent
(`UNIQUE(kind, source_ref)`), shuning uchun DLQ'dan qayta yuborish xavfsiz.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.services import ledger
from app.services.deps import Deps


def register(consumer: EventConsumer, deps: Deps) -> None:
    @consumer.on(Events.BOOKING_COMPLETED)
    async def on_booking_completed(session: AsyncSession, event: Envelope) -> None:
        data = event.data
        await ledger.record_earning(
            session,
            data,
            status="completed",
            eligible_at=datetime.fromisoformat(data["completed_at"]),
            services=[i["service_name"] for i in data["items"]],
        )

    @consumer.on(Events.BOOKING_NO_SHOW)
    async def on_booking_no_show(session: AsyncSession, event: Envelope) -> None:
        # Mijoz kelmadi — shifokor vaqt sarflagan; shifokor kelmadi — to'liq refund keyin nolga tushiradi
        data = event.data
        await ledger.record_earning(
            session, data, status="no_show", eligible_at=datetime.fromisoformat(data["end_at"]), services=[]
        )

    @consumer.on(Events.BOOKING_CANCELLED)
    async def on_booking_cancelled(session: AsyncSession, event: Envelope) -> None:
        # Faqat to'langan (tasdiqlangan) bron: refund ulushi refund.succeeded bilan ayiriladi, qolgani — jarima
        if event.data["previous_status"] != "confirmed":
            return
        await ledger.record_earning(session, event.data, status="cancelled", eligible_at=event.occurred_at, services=[])

    @consumer.on(Events.REFUND_SUCCEEDED)
    async def on_refund_succeeded(session: AsyncSession, event: Envelope) -> None:
        await ledger.record_refund(session, event.data)

    @consumer.on(Events.PAYMENT_REVERSED)
    async def on_payment_reversed(session: AsyncSession, event: Envelope) -> None:
        await ledger.record_reversal(session, event.data)

    @consumer.on(Events.ADMIN_SETTINGS_CHANGED)
    async def on_settings_changed(session: AsyncSession, event: Envelope) -> None:
        await deps.settings.invalidate()
