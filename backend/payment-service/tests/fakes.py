"""Upstream (booking, admin) va provayderlarning xotiradagi soxta nusxalari. booking'ning HTTP kontrakti —
test_clients.py (contracts/openapi/booking-service.internal.yaml)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.clients.providers import ManualRefundRequired, ProviderError, RefundResult
from app.clients.upstream import Payable

DOCTOR = uuid.UUID("11111111-1111-7111-8111-111111111111")
CLINIC = uuid.UUID("22222222-2222-7222-8222-222222222222")

PLATFORM: dict[str, Any] = {
    "commission": {"platform_bp": 1500, "payme_fee_bp": 100, "click_fee_bp": 100},
    "fiscal": {
        "default_mxik_code": "06101001001000000",
        "default_package_code": "1500",
        "default_vat_percent": 0,
        "tin": "123456789",
    },
}


def money(total: int) -> dict[str, Any]:
    platform, provider = total * 15 // 100, total // 100
    return {
        "total_tiyin": total,
        "platform_fee_tiyin": platform,
        "provider_fee_tiyin": provider,
        "doctor_payout_tiyin": total - platform - provider,
        "discount_tiyin": 0,
        "discount_borne_by": "",
    }


class FakeSettings:
    def __init__(self) -> None:
        self.values = PLATFORM
        self.invalidated = 0

    async def get(self) -> dict[str, Any]:
        return self.values

    async def invalidate(self) -> None:
        self.invalidated += 1


class FakeBooking:
    def __init__(self) -> None:
        self.bookings: dict[uuid.UUID, dict[str, Any]] = {}
        self.extended: list[uuid.UUID] = []
        self.fail_extend = False

    def add(
        self,
        client_id: uuid.UUID,
        *,
        total: int = 15_000_000,
        prepay: int | None = None,
        mode: str = "prepaid",
        payable: bool = True,
        items: list[tuple[str, int]] | None = None,
    ) -> uuid.UUID:
        booking_id = uuid.uuid4()
        rows = items or [("Konsultatsiya", total)]
        self.bookings[booking_id] = {
            "booking_id": str(booking_id),
            "number": f"MB-{len(self.bookings) + 1:06d}",
            "status": "pending_payment",
            "client_id": str(client_id),
            "doctor_id": str(DOCTOR),
            "clinic_id": str(CLINIC),
            "payable": payable,
            "payment_mode": mode,
            "prepay_tiyin": total if prepay is None else prepay,
            "total_tiyin": total,
            "payment_due_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "money": money(total),
            "items": [
                {
                    "service_name": name,
                    "price_tiyin": price,
                    "mxik_code": "06101001001000000",
                    "package_code": "1500",
                    "vat_percent": 0,
                }
                for name, price in rows
            ],
        }
        return booking_id

    def expire(self, booking_id: uuid.UUID) -> None:
        self.bookings[booking_id] |= {"status": "expired", "payable": False}

    async def payable(self, booking_id: uuid.UUID) -> Payable | None:
        data = self.bookings.get(booking_id)
        return Payable.parse(data) if data else None

    async def extend_payment(self, booking_id: uuid.UUID) -> datetime | None:
        if self.fail_extend:
            raise RuntimeError("booking-service down")
        self.extended.append(booking_id)
        return datetime.now(UTC) + timedelta(minutes=15)

    async def aclose(self) -> None:
        return None


class FakeProvider:
    """`outcome`: "ok" | "manual" | "error". Chaqiruvlar `calls` da."""

    def __init__(self, name: str, outcome: str = "ok") -> None:
        self.name = name
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.fiscal: list[dict[str, Any]] = []

    async def refund(self, *, paydoc_id: str | None, amount_tiyin: int, payment_amount_tiyin: int) -> RefundResult:
        self.calls.append({"paydoc_id": paydoc_id, "amount_tiyin": amount_tiyin})
        if self.outcome == "manual":
            raise ManualRefundRequired(f"{self.name}: kabinetda")
        if self.outcome == "error":
            raise ProviderError(f"{self.name} 503")
        return RefundResult(external_refund_id=f"{self.name}-r-{len(self.calls)}")

    async def submit_fiscal(self, *, paydoc_id: str | None, amount_tiyin: int, items: list[dict[str, Any]]) -> None:
        if self.outcome == "error":
            raise ProviderError("click 503")
        self.fiscal.append({"paydoc_id": paydoc_id, "amount_tiyin": amount_tiyin, "items": items})


class FakeDeps:
    def __init__(self) -> None:
        self.booking = FakeBooking()
        self.settings = FakeSettings()
        self.payme = FakeProvider("payme", outcome="manual")  # Merchant API refund qila olmaydi
        self.click = FakeProvider("click")

    def provider(self, name: str) -> FakeProvider:
        return self.payme if name == "payme" else self.click

    async def aclose(self) -> None:
        return None
