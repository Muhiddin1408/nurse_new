"""Sinxron bog'liqliklar (reestr: booking; admin — sozlamalar) — contracts/openapi/booking-service.internal.yaml.

booking `review-eligibility` — faqat FALLBACK: `booking.completed` proyeksiyasi hali kelmagan bo'lsa (event kechikishi).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from app.clients.base import BaseClient
from app.core.config import get_settings


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str
    doctor_id: UUID | None
    completed_at: datetime | None

    @classmethod
    def parse(cls, d: dict[str, Any]) -> Eligibility:
        return cls(
            eligible=bool(d["eligible"]),
            reason=str(d.get("reason") or ""),
            doctor_id=UUID(d["doctor_id"]) if d.get("doctor_id") else None,
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
        )


class BookingClient(BaseClient):
    target: ClassVar[str] = "booking-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().booking_service_url)

    async def review_eligibility(self, booking_id: UUID, client_id: UUID) -> Eligibility:
        data = await self.http.get_json(
            f"/internal/v1/bookings/{booking_id}/review-eligibility", params={"client_id": str(client_id)}
        )
        return Eligibility.parse(data)


class AdminClient(BaseClient):
    """Faqat `PlatformSettingsClient` uchun transport (`sla.review_window_days`)."""

    target: ClassVar[str] = "admin-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().admin_service_url)
