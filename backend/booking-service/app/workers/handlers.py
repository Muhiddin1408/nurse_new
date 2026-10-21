"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi BITTA
tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang. Sinxron chaqiruv (schedule confirm) xatosi —
handler yiqiladi, consumer retry qiladi (5s → 30s → 5m → DLQ).

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope, EventConsumer
from medbron_common.events import Events

from app.services import lifecycle
from app.services.deps import Deps


def register(consumer: EventConsumer, deps: Deps) -> None:
    @consumer.on(Events.PAYMENT_CAPTURED)
    async def on_payment_captured(session: AsyncSession, event: Envelope) -> None:
        await lifecycle.on_payment_captured(session, deps, event.data)

    @consumer.on(Events.PAYMENT_REVERSED)
    async def on_payment_reversed(session: AsyncSession, event: Envelope) -> None:
        await lifecycle.on_payment_reversed(session, deps, event.data)

    @consumer.on(Events.REFUND_SUCCEEDED)
    async def on_refund_succeeded(session: AsyncSession, event: Envelope) -> None:
        await lifecycle.on_refund_succeeded(session, event.data)

    @consumer.on(Events.SLOT_RELEASED)
    async def on_slot_released(session: AsyncSession, event: Envelope) -> None:
        await lifecycle.on_slot_released(session, event.data)

    @consumer.on(Events.DOCTOR_SUSPENDED)
    async def on_doctor_suspended(session: AsyncSession, event: Envelope) -> None:
        await lifecycle.cancel_future(
            session,
            deps,
            doctor_id=UUID(event.data["doctor_id"]),
            reason_code="doctor_suspended",
            reason_text="Shifokor faoliyati to'xtatildi",
        )

    @consumer.on(Events.CLINIC_SUSPENDED)
    async def on_clinic_suspended(session: AsyncSession, event: Envelope) -> None:
        await lifecycle.cancel_future(
            session,
            deps,
            clinic_id=UUID(event.data["clinic_id"]),
            reason_code="clinic_suspended",
            reason_text="Klinika faoliyati to'xtatildi",
        )

    @consumer.on(Events.AFFILIATION_ENDED)
    async def on_affiliation_ended(session: AsyncSession, event: Envelope) -> None:
        # Shu klinikadagi shu shifokorning kelajakdagi bronlari
        await lifecycle.cancel_affiliation(
            session, deps, doctor_id=UUID(event.data["doctor_id"]), clinic_id=UUID(event.data["clinic_id"])
        )

    @consumer.on(Events.USER_DELETION_REQUESTED)
    async def on_user_deletion(session: AsyncSession, event: Envelope) -> None:
        # user-service faol bron bo'lsa o'chirishni rad etadi; proyeksiya kechikkan holat uchun (ARCHITECTURE 6.10)
        await lifecycle.cancel_future(
            session,
            deps,
            client_id=UUID(event.data["user_id"]),
            reason_code="account_deleted",
            reason_text="Akkaunt o'chirildi",
        )

    @consumer.on(Events.ADMIN_SETTINGS_CHANGED)
    async def on_settings_changed(session: AsyncSession, event: Envelope) -> None:
        await deps.settings.invalidate()
