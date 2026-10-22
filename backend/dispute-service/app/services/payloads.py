"""Chiquvchi eventlar (contracts/events/dispute.*.json). Har biri biznes yozuvi bilan BITTA tranzaksiyada."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.events import Events

from app.events import outbox
from app.models import Dispute


def opened(session: AsyncSession, d: Dispute) -> None:
    outbox.add_event(
        session,
        Events.DISPUTE_OPENED,
        aggregate_id=d.id,
        data={
            "dispute_id": d.id,
            "booking_id": d.booking_id,
            "raised_by": d.client_id,
            "reason": d.reason,
            "due_at": d.due_at,
        },
    )


def resolved(session: AsyncSession, d: Dispute) -> None:
    outbox.add_event(
        session,
        Events.DISPUTE_RESOLVED,
        aggregate_id=d.id,
        data={
            "dispute_id": d.id,
            "booking_id": d.booking_id,
            "raised_by": d.client_id,
            "resolution": d.resolution,
            "refund_tiyin": d.refund_tiyin,
            "doctor_liable": bool(d.doctor_liable),
            "resolved_by": d.resolved_by,
            "resolved_at": d.resolved_at,
        },
    )


def sla_breached(session: AsyncSession, d: Dispute) -> None:
    outbox.add_event(
        session,
        Events.DISPUTE_SLA_BREACHED,
        aggregate_id=d.id,
        data={"dispute_id": d.id, "booking_id": d.booking_id, "due_at": d.due_at},
    )
