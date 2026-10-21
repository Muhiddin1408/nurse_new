"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi
BITTA tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

    @consumer.on(Events.BOOKING_CONFIRMED)
    async def on_booking_confirmed(session: AsyncSession, event: Envelope) -> None:
        data = BookingConfirmed.model_validate(event.data)
        await services.bookings.confirm(session, data)

Katalogda (`contracts/events/catalog.yaml`) shu servis consumer deb yozilgan HAR event uchun
handler bo'lishi shart — `scripts/check-contracts.py` tekshiradi.
"""

from __future__ import annotations

from medbron_common.eventbus import EventConsumer


def register(consumer: EventConsumer) -> None:
    """Handlerlarni shu yerda `consumer.on(...)` bilan ro'yxatdan o'tkazing."""
