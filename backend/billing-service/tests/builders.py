"""Kontraktga mos event payload'lari (sample_data ustiga) — testlar faqat muhim maydonlarni beradi."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from medbron_common.events import Events
from medbron_common.testing import sample_data

DOCTOR = uuid.UUID("11111111-1111-7111-8111-111111111111")
CLINIC = uuid.UUID("22222222-2222-7222-8222-222222222222")

MONEY: dict[str, Any] = {
    "total_tiyin": 15_000_000,
    "platform_fee_tiyin": 2_250_000,
    "provider_fee_tiyin": 150_000,
    "doctor_payout_tiyin": 12_600_000,
    "discount_tiyin": 0,
    "discount_borne_by": "",
}


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def days_ago(days: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _booking(**kw: Any) -> dict[str, Any]:
    start: datetime = kw.pop("start", days_ago(8))
    return {
        "booking_id": str(kw.pop("booking_id", uuid.uuid4())),
        "number": kw.pop("number", f"MB-{uuid.uuid4().hex[:6].upper()}"),
        "doctor_id": str(kw.pop("doctor_id", DOCTOR)),
        "clinic_id": str(c) if (c := kw.pop("clinic_id", None)) else None,
        "start_at": iso(start),
        "end_at": iso(start + timedelta(minutes=30)),
        "paid_tiyin": kw.pop("paid", 15_000_000),
        "money": kw.pop("money", MONEY),
        **kw,
    }


def completed(**kw: Any) -> dict[str, Any]:
    completed_at = kw.pop("completed_at", None)
    data = _booking(**kw)
    data["completed_at"] = iso(completed_at or datetime.fromisoformat(data["end_at"]))
    data.setdefault(
        "items",
        [
            {
                "service_id": str(uuid.uuid4()),
                "service_name": "Konsultatsiya",
                "price_tiyin": 15_000_000,
                "duration_minutes": 30,
            }
        ],
    )
    return sample_data(Events.BOOKING_COMPLETED, payment_mode="prepaid", **data)


def no_show(by: str = "client", **kw: Any) -> dict[str, Any]:
    return sample_data(Events.BOOKING_NO_SHOW, no_show_by=by, **_booking(**kw))


def cancelled(previous: str = "confirmed", refund: int = 0, **kw: Any) -> dict[str, Any]:
    data = _booking(**kw)
    return sample_data(
        Events.BOOKING_CANCELLED,
        previous_status=previous,
        refund_tiyin=refund,
        penalty_tiyin=max(0, data["paid_tiyin"] - refund),
        **data,
    )


def refund(booking_id: str, amount: int, doctor_share: int, *, at: datetime | None = None) -> dict[str, Any]:
    return sample_data(
        Events.REFUND_SUCCEEDED,
        booking_id=booking_id,
        amount_tiyin=amount,
        doctor_share_tiyin=doctor_share,
        completed_at=iso(at or days_ago(8)),
    )


def reversed_(booking_id: str, amount: int, doctor_share: int, *, at: datetime | None = None) -> dict[str, Any]:
    return sample_data(
        Events.PAYMENT_REVERSED,
        booking_id=booking_id,
        amount_tiyin=amount,
        doctor_share_tiyin=doctor_share,
        reversed_at=iso(at or days_ago(8)),
    )
