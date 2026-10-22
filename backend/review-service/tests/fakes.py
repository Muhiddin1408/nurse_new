"""Upstream'larning xotiradagi soxta nusxalari (booking HTTP kontrakti — test_clients.py)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.clients.upstream import Eligibility

PLATFORM: dict[str, Any] = {"sla": {"review_window_days": 30, "dispute_hours": 48, "moderation_hours": 48}}


class FakeSettings:
    def __init__(self) -> None:
        self.values = PLATFORM
        self.invalidated = 0

    async def get(self) -> dict[str, Any]:
        return self.values

    async def invalidate(self) -> None:
        self.invalidated += 1


class FakeBooking:
    """booking `review-eligibility`: (booking_id) → (client_id, doctor_id, completed_at)."""

    def __init__(self) -> None:
        self.completed: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID, datetime]] = {}
        self.calls = 0

    def add(self, client_id: uuid.UUID, doctor_id: uuid.UUID, *, days_ago: int = 1) -> uuid.UUID:
        booking_id = uuid.uuid4()
        self.completed[booking_id] = (client_id, doctor_id, datetime.now(UTC) - timedelta(days=days_ago))
        return booking_id

    async def review_eligibility(self, booking_id: uuid.UUID, client_id: uuid.UUID) -> Eligibility:
        self.calls += 1
        row = self.completed.get(booking_id)
        if row is None:
            return Eligibility(False, "not_completed", None, None)
        if row[0] != client_id:
            return Eligibility(False, "not_owner", None, None)
        return Eligibility(True, "", row[1], row[2])

    async def aclose(self) -> None:
        return None


class FakeDeps:
    def __init__(self) -> None:
        self.booking = FakeBooking()
        self.settings = FakeSettings()

    async def aclose(self) -> None:
        return None
