"""Chiquvchi eventlar (contracts/events/*.json). Har biri biznes yozuvi bilan BITTA tranzaksiyada outbox'ga."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.events import Events

from app.events import outbox
from app.models import Discrepancy, Payment, Refund


def _base(p: Payment) -> dict[str, object]:
    return {"payment_id": p.id, "booking_id": p.booking_id, "client_id": p.client_id, "provider": p.provider}


def created(session: AsyncSession, p: Payment) -> None:
    outbox.add_event(
        session, Events.PAYMENT_CREATED, aggregate_id=p.id, data={**_base(p), "amount_tiyin": p.amount_tiyin}
    )


def captured(session: AsyncSession, p: Payment) -> None:
    outbox.add_event(
        session,
        Events.PAYMENT_CAPTURED,
        aggregate_id=p.id,
        data={**_base(p), "amount_tiyin": p.amount_tiyin, "captured_at": p.captured_at or datetime.now(UTC)},
    )


def failed(session: AsyncSession, p: Payment, *, reason: str) -> None:
    outbox.add_event(session, Events.PAYMENT_FAILED, aggregate_id=p.id, data={**_base(p), "reason": reason})


def reversed_(
    session: AsyncSession, p: Payment, *, amount_tiyin: int, doctor_share_tiyin: int, provider_reason: int | None
) -> None:
    outbox.add_event(
        session,
        Events.PAYMENT_REVERSED,
        aggregate_id=p.id,
        data={
            **_base(p),
            "amount_tiyin": amount_tiyin,
            "doctor_share_tiyin": doctor_share_tiyin,
            "reversed_at": datetime.now(UTC),
            "provider_reason": provider_reason,
        },
    )


def refund_succeeded(session: AsyncSession, r: Refund, p: Payment) -> None:
    outbox.add_event(
        session,
        Events.REFUND_SUCCEEDED,
        aggregate_id=r.id,
        data={
            "refund_id": r.id,
            "payment_id": r.payment_id,
            "booking_id": r.booking_id,
            "client_id": p.client_id,
            "amount_tiyin": r.amount_tiyin,
            "doctor_share_tiyin": r.doctor_share_tiyin,
            "reason": r.reason,
            "source": r.source,
            # Manba obyekti (bron / to'lov / nizo ID). Admin refundida bu Idempotency-Key — tashqariga chiqmaydi
            "source_ref": None if r.source == "admin" else r.source_ref,
            "completed_at": r.completed_at or datetime.now(UTC),
        },
    )


def refund_failed(session: AsyncSession, r: Refund) -> None:
    outbox.add_event(
        session,
        Events.REFUND_FAILED,
        aggregate_id=r.id,
        data={
            "refund_id": r.id,
            "payment_id": r.payment_id,
            "booking_id": r.booking_id,
            "amount_tiyin": r.amount_tiyin,
            "attempts": r.attempts,
            "last_error": r.last_error[:500],
        },
    )


def discrepancy_found(session: AsyncSession, d: Discrepancy) -> None:
    outbox.add_event(
        session,
        Events.PAYMENT_DISCREPANCY_FOUND,
        aggregate_id=d.id,
        data={
            "day": d.day.isoformat(),
            "provider": d.provider or None,
            "type": d.type,
            "payment_id": str(d.payment_id) if d.payment_id else None,
            "external_id": d.external_id or None,
            "details": {
                **d.details,
                "booking_id": str(d.booking_id) if d.booking_id else None,
                "local_tiyin": d.local_tiyin,
                "provider_tiyin": d.provider_tiyin,
            },
        },
    )
