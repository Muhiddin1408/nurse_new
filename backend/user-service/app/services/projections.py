"""`booking.*` → `booking_states` proyeksiyasi (faqat "faol bron bormi" uchun)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope
from medbron_common.events import Events

from app.repositories import users as repo

# Event turi → bron holati (`booking.created` da holat payload'da: pending_payment | confirmed)
STATUS_BY_EVENT = {
    Events.BOOKING_CONFIRMED: "confirmed",
    Events.BOOKING_CANCELLED: "cancelled",
    Events.BOOKING_EXPIRED: "expired",
    Events.BOOKING_COMPLETED: "completed",
    Events.BOOKING_NO_SHOW: "no_show",
}


async def apply_booking_event(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    status = data["status"] if event.event_type == Events.BOOKING_CREATED else STATUS_BY_EVENT[event.event_type]
    await repo.apply_booking_state(
        session,
        booking_id=UUID(data["booking_id"]),
        user_id=UUID(data["client_id"]),
        patient_id=UUID(data["patient_id"]),
        status=status,
        changed_at=event.occurred_at,
    )
