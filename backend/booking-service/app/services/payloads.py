"""`booking.*` eventlari — contracts/events/booking.*.v1.json bilan aynan bir xil maydonlar. Hammasi outbox
orqali, holat o'zgarishi bilan BITTA tranzaksiyada (`emit`). Telefon raqami eventlarga kirmaydi (A10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.events import Events

from app.events import outbox
from app.models import Booking, BookingItem


def _base(b: Booking) -> dict[str, Any]:
    return {
        "booking_id": b.id,
        "number": b.number,
        "client_id": b.client_id,
        "patient_id": b.patient_id,
        "doctor_id": b.doctor_id,
        "clinic_id": b.clinic_id,
        "slot_id": b.slot_id,
        "place": b.place,
        "start_at": b.start_at,
        "end_at": b.end_at,
    }


def money(b: Booking) -> dict[str, Any]:
    return {
        "total_tiyin": b.total_tiyin,
        "platform_fee_tiyin": b.platform_fee_tiyin,
        "provider_fee_tiyin": b.provider_fee_tiyin,
        "doctor_payout_tiyin": b.doctor_payout_tiyin,
        "discount_tiyin": b.discount_tiyin,
        "discount_borne_by": b.discount_borne_by,
    }


def items(rows: list[BookingItem]) -> list[dict[str, Any]]:
    return [
        {
            "service_id": i.service_id,
            "service_name": i.service_name,
            "price_tiyin": i.price_tiyin,
            "duration_minutes": i.duration_minutes,
        }
        for i in rows
    ]


def created(session: AsyncSession, b: Booking, rows: list[BookingItem]) -> None:
    outbox.add_event(
        session,
        Events.BOOKING_CREATED,
        aggregate_id=b.id,
        data={
            **_base(b),
            "status": b.status,
            "payment_mode": b.payment_mode,
            "prepay_tiyin": b.prepay_tiyin,
            "payment_due_at": b.payment_due_at,
            "items": items(rows),
            "money": money(b),
            "promo_code": b.promo_code,
        },
    )


def confirmed(session: AsyncSession, b: Booking) -> None:
    assert b.confirmed_at is not None
    outbox.add_event(
        session,
        Events.BOOKING_CONFIRMED,
        aggregate_id=b.id,
        data={
            **_base(b),
            "payment_mode": b.payment_mode,
            "payment_id": b.payment_id,
            "confirmed_at": b.confirmed_at,
            "money": money(b),
            "promo_code": b.promo_code,
        },
    )


def cancelled(
    session: AsyncSession, b: Booking, *, previous_status: str, refund_tiyin: int, penalty_tiyin: int
) -> None:
    outbox.add_event(
        session,
        Events.BOOKING_CANCELLED,
        aggregate_id=b.id,
        data={
            **_base(b),
            "cancelled_by": b.cancelled_by,
            "reason_code": b.cancel_reason_code,
            "reason_text": b.cancel_reason_text,
            "paid_tiyin": b.paid_tiyin,
            "refund_tiyin": refund_tiyin,
            "penalty_tiyin": penalty_tiyin,
            "previous_status": "confirmed" if previous_status == "in_progress" else previous_status,
        },
    )


def expired(session: AsyncSession, b: Booking) -> None:
    assert b.payment_due_at is not None
    outbox.add_event(
        session, Events.BOOKING_EXPIRED, aggregate_id=b.id, data={**_base(b), "payment_due_at": b.payment_due_at}
    )


def rescheduled(session: AsyncSession, b: Booking, *, old_slot_id: Any, old_start_at: datetime, by: str) -> None:
    outbox.add_event(
        session,
        Events.BOOKING_RESCHEDULED,
        aggregate_id=b.id,
        data={
            "booking_id": b.id,
            "number": b.number,
            "client_id": b.client_id,
            "doctor_id": b.doctor_id,
            "old_slot_id": old_slot_id,
            "new_slot_id": b.slot_id,
            "old_start_at": old_start_at,
            "new_start_at": b.start_at,
            "new_end_at": b.end_at,
            "rescheduled_by": by,
            "rescheduled_count": b.rescheduled_count,
        },
    )


def started(session: AsyncSession, b: Booking) -> None:
    assert b.started_at is not None
    outbox.add_event(
        session,
        Events.BOOKING_STARTED,
        aggregate_id=b.id,
        data={"booking_id": b.id, "client_id": b.client_id, "doctor_id": b.doctor_id, "started_at": b.started_at},
    )


def completed(session: AsyncSession, b: Booking, rows: list[BookingItem]) -> None:
    assert b.completed_at is not None
    outbox.add_event(
        session,
        Events.BOOKING_COMPLETED,
        aggregate_id=b.id,
        data={
            **_base(b),
            "completed_at": b.completed_at,
            "completed_by": b.completed_by,
            "payment_mode": b.payment_mode,
            "paid_tiyin": b.paid_tiyin,
            "money": money(b),
            "items": items(rows),
        },
    )


def no_show(session: AsyncSession, b: Booking, *, refund_tiyin: int) -> None:
    outbox.add_event(
        session,
        Events.BOOKING_NO_SHOW,
        aggregate_id=b.id,
        data={
            **_base(b),
            "no_show_by": b.no_show_by,
            "marked_by": b.completed_by,
            "paid_tiyin": b.paid_tiyin,
            "refund_tiyin": refund_tiyin,
            "money": money(b),
        },
    )


def confirmation_failed(session: AsyncSession, b: Booking, *, payment_id: Any, amount_tiyin: int, reason: str) -> None:
    outbox.add_event(
        session,
        Events.BOOKING_CONFIRMATION_FAILED,
        aggregate_id=b.id,
        data={
            "booking_id": b.id,
            "client_id": b.client_id,
            "payment_id": payment_id,
            "amount_tiyin": amount_tiyin,
            "reason": reason,
        },
    )
