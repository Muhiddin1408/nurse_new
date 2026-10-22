"""Sinxron bog'liqliklar (reestr: booking, admin) — contracts/openapi/booking-service.internal.yaml.

payment → booking yo'nalishi bir tomonlama: booking payment'ni HECH QACHON sinxron chaqirmaydi (ADR-0006),
natija faqat event orqali (`payment.captured` → confirm, `booking.confirmation_failed` → refund).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from medbron_common.http_client import UpstreamError

from app.clients.base import BaseClient
from app.core.config import get_settings


@dataclass(frozen=True)
class Payable:
    booking_id: UUID
    number: str
    status: str
    client_id: UUID
    doctor_id: UUID
    clinic_id: UUID | None
    payable: bool
    payment_mode: str
    prepay_tiyin: int
    total_tiyin: int
    payment_due_at: datetime | None
    money: dict[str, Any]
    items: list[dict[str, Any]]

    @classmethod
    def parse(cls, d: dict[str, Any]) -> Payable:
        return cls(
            booking_id=UUID(d["booking_id"]),
            number=d["number"],
            status=d["status"],
            client_id=UUID(d["client_id"]),
            doctor_id=UUID(d["doctor_id"]),
            clinic_id=UUID(d["clinic_id"]) if d.get("clinic_id") else None,
            payable=bool(d["payable"]),
            payment_mode=d["payment_mode"],
            prepay_tiyin=int(d["prepay_tiyin"]),
            total_tiyin=int(d["total_tiyin"]),
            payment_due_at=datetime.fromisoformat(d["payment_due_at"]) if d.get("payment_due_at") else None,
            money=dict(d["money"]),
            items=list(d["items"]),
        )


class BookingClient(BaseClient):
    target: ClassVar[str] = "booking-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().booking_service_url)

    async def payable(self, booking_id: UUID) -> Payable | None:
        try:
            data = await self.http.get_json(f"/internal/v1/bookings/{booking_id}/payable")
        except UpstreamError as exc:
            if exc.upstream_status == 404:
                return None
            raise
        return Payable.parse(data)

    async def extend_payment(self, booking_id: UUID) -> datetime | None:
        """Mijoz provayder sahifasida to'layapti (B3) — hold va payment_due_at uzaytiriladi.
        409 (bron endi to'lanmaydi) — None: Perform baribir `payment.captured` → booking qaror qiladi."""
        try:
            data = await self.http.post_json(f"/internal/v1/bookings/{booking_id}/extend-payment")
        except UpstreamError as exc:
            if exc.upstream_status in (404, 409):
                return None
            raise
        due = (data or {}).get("payment_due_at")
        return datetime.fromisoformat(due) if due else None


class AdminClient(BaseClient):
    """Faqat `PlatformSettingsClient` uchun transport (fiskal TIN)."""

    target: ClassVar[str] = "admin-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().admin_service_url)
