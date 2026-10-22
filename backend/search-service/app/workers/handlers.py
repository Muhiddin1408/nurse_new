"""Event handlerlar → proyeksiya → ES (services/inbound.py). Har handler consumer tranzaksiyasi ichida;
ES xatosi ko'tariladi → rollback → consumer retry (5s → 30s → 5m → DLQ). ES yozuvi external versiya bilan —
qayta urinish xavfsiz.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.models.projection import APPROVED, SUSPENDED
from app.services import inbound


def register(consumer: EventConsumer) -> None:
    @consumer.on(Events.DOCTOR_APPROVED)
    async def on_doctor_approved(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_doctor_profile(session, event.data, event.occurred_at, approved=True)

    @consumer.on(Events.DOCTOR_PROFILE_UPDATED)
    async def on_doctor_profile_updated(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_doctor_profile(session, event.data, event.occurred_at, approved=False)

    @consumer.on(Events.DOCTOR_SUSPENDED)
    async def on_doctor_suspended(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_doctor_status(session, event.data, event.occurred_at, status=SUSPENDED)

    @consumer.on(Events.DOCTOR_REINSTATED)
    async def on_doctor_reinstated(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_doctor_status(session, event.data, event.occurred_at, status=APPROVED)

    @consumer.on(Events.CLINIC_PUBLISHED)
    async def on_clinic_published(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_clinic_info(session, event.data, event.occurred_at, status="active")

    @consumer.on(Events.CLINIC_UPDATED)
    async def on_clinic_updated(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_clinic_info(session, event.data, event.occurred_at, status=event.data.get("status"))

    @consumer.on(Events.CLINIC_SUSPENDED)
    async def on_clinic_suspended(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_clinic_suspended(session, event.data, event.occurred_at)

    async def on_affiliation(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_affiliation(session, event.event_type, event.data, event.occurred_at)

    consumer.on(Events.AFFILIATION_ACCEPTED)(on_affiliation)
    consumer.on(Events.AFFILIATION_PAUSED)(on_affiliation)
    consumer.on(Events.AFFILIATION_RESUMED)(on_affiliation)
    consumer.on(Events.AFFILIATION_ENDED)(on_affiliation)

    @consumer.on(Events.SERVICE_CREATED)
    async def on_service_created(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_service(session, event.data, event.occurred_at, deactivated=False)

    @consumer.on(Events.SERVICE_UPDATED)
    async def on_service_updated(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_service(session, event.data, event.occurred_at, deactivated=False)

    @consumer.on(Events.SERVICE_DEACTIVATED)
    async def on_service_deactivated(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_service(session, event.data, event.occurred_at, deactivated=True)

    @consumer.on(Events.SCHEDULE_AVAILABILITY_CHANGED)
    async def on_availability_changed(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_availability(session, event.data, event.occurred_at)

    @consumer.on(Events.RATING_RECALCULATED)
    async def on_rating_recalculated(session: AsyncSession, event: Envelope) -> None:
        await inbound.on_rating(session, event.data, event.occurred_at)
