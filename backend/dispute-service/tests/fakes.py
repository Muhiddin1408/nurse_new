"""Upstream servislarning (booking, file, admin) xotiradagi soxta nusxalari; HTTP kontrakti — test_clients.py."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.clients.upstream import FileRejected

DOCTOR = uuid.UUID("11111111-1111-7111-8111-111111111111")

PLATFORM: dict[str, Any] = {"sla": {"dispute_hours": 48, "review_window_days": 30, "moderation_hours": 48}}


class FakeSettings:
    def __init__(self) -> None:
        self.values = {k: dict(v) for k, v in PLATFORM.items()}
        self.invalidated = 0

    async def get(self) -> dict[str, Any]:
        return self.values

    async def invalidate(self) -> None:
        self.invalidated += 1


class FakeBooking:
    def __init__(self) -> None:
        self.bookings: dict[uuid.UUID, dict[str, Any]] = {}

    def add(self, client_id: uuid.UUID, *, status: str = "completed", paid: int = 15_000_000) -> uuid.UUID:
        booking_id = uuid.uuid4()
        self.bookings[booking_id] = {
            "booking_id": str(booking_id),
            "number": f"MB-{len(self.bookings) + 1:06d}",
            "status": status,
            "client_id": str(client_id),
            "doctor_id": str(DOCTOR),
            "start_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "paid_tiyin": paid,
            "total_tiyin": max(paid, 15_000_000),
        }
        return booking_id

    async def summary(self, booking_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any] | None:
        data = self.bookings.get(booking_id)
        return data if data and data["client_id"] == str(user_id) else None

    async def aclose(self) -> None:
        return None


class FakeFiles:
    """file-service: fayl egasi (`owner_user_id`) va biriktirish holati (`owner_ref`)."""

    def __init__(self) -> None:
        self.files: dict[uuid.UUID, dict[str, Any]] = {}
        self.urls: list[tuple[uuid.UUID, str]] = []
        self.url_down = False

    def upload(self, owner: uuid.UUID, content_type: str = "application/pdf") -> uuid.UUID:
        file_id = uuid.uuid4()
        self.files[file_id] = {"owner": owner, "ref": None, "content_type": content_type}
        return file_id

    async def attach(self, file_id: uuid.UUID, *, owner_user_id: uuid.UUID, owner_ref: str) -> dict[str, Any]:
        f = self.files.get(file_id)
        if f is None or f["owner"] != owner_user_id:
            raise FileRejected(404, "FILE_NOT_FOUND")
        if f["ref"] not in (None, owner_ref):
            raise FileRejected(409, "INVALID_FILE_STATE")
        f["ref"] = owner_ref
        return {"id": str(file_id), "status": "attached", "content_type": f["content_type"]}

    async def url(self, file_id: uuid.UUID, *, viewer_type: str, viewer_id: str, ttl: int) -> str:
        if self.url_down:
            from medbron_common.http_client import UpstreamUnavailable

            raise UpstreamUnavailable("file-service")
        self.urls.append((file_id, viewer_id))
        return f"https://s3.local/private/{file_id}?ttl={ttl}"


class FakeDeps:
    def __init__(self) -> None:
        self.booking = FakeBooking()
        self.files = FakeFiles()
        self.settings = FakeSettings()

    async def aclose(self) -> None:
        return None
