"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi
BITTA tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

Katalogda (`contracts/events/catalog.yaml`) shu servis consumer deb yozilgan HAR event uchun
handler bo'lishi shart — `scripts/check-contracts.py` tekshiradi.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.services import projections, users


def register(consumer: EventConsumer) -> None:
    @consumer.on(Events.USER_DELETION_REQUESTED)
    async def on_deletion_requested(session: AsyncSession, event: Envelope) -> None:
        await users.deactivate_for_deletion(session, UUID(event.data["user_id"]))

    @consumer.on(Events.DOCTOR_APPROVED)
    async def on_doctor_approved(session: AsyncSession, event: Envelope) -> None:
        await projections.doctor_status(
            session,
            user_id=UUID(event.data["user_id"]),
            doctor_id=UUID(event.data["doctor_id"]),
            status="approved",
            occurred_at=event.occurred_at,
            requires_existing=False,
        )

    @consumer.on(Events.DOCTOR_SUSPENDED)
    async def on_doctor_suspended(session: AsyncSession, event: Envelope) -> None:
        # Rol saqlanadi (ARCHITECTURE 6.9): shifokor kabinetiga kira oladi, bron qabul qilishni catalog to'xtatadi.
        await projections.doctor_status(
            session,
            user_id=UUID(event.data["user_id"]),
            doctor_id=UUID(event.data["doctor_id"]),
            status="suspended",
            occurred_at=event.occurred_at,
            requires_existing=True,
        )

    @consumer.on(Events.DOCTOR_REINSTATED)
    async def on_doctor_reinstated(session: AsyncSession, event: Envelope) -> None:
        await projections.doctor_status(
            session,
            user_id=UUID(event.data["user_id"]),
            doctor_id=UUID(event.data["doctor_id"]),
            status="approved",
            occurred_at=event.occurred_at,
            requires_existing=True,
        )

    @consumer.on(Events.CLINIC_MEMBER_ADDED)
    async def on_member_added(session: AsyncSession, event: Envelope) -> None:
        await projections.clinic_membership(
            session,
            user_id=UUID(event.data["user_id"]),
            clinic_id=UUID(event.data["clinic_id"]),
            active=True,
            occurred_at=event.occurred_at,
        )

    @consumer.on(Events.CLINIC_MEMBER_REMOVED)
    async def on_member_removed(session: AsyncSession, event: Envelope) -> None:
        await projections.clinic_membership(
            session,
            user_id=UUID(event.data["user_id"]),
            clinic_id=UUID(event.data["clinic_id"]),
            active=False,
            occurred_at=event.occurred_at,
        )
