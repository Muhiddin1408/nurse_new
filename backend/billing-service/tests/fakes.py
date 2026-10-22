"""Upstream servislarning xotiradagi soxta nusxalari (catalog access, admin sozlamalari)."""

from __future__ import annotations

import uuid
from typing import Any

PLATFORM: dict[str, Any] = {
    "payout": {"min_amount_tiyin": 5_000_000, "weekday": 0},
    "commission": {"platform_bp": 1500, "payme_fee_bp": 100, "click_fee_bp": 100},
}


class FakeSettings:
    def __init__(self) -> None:
        self.values = {k: dict(v) for k, v in PLATFORM.items()}
        self.invalidated = 0

    async def get(self) -> dict[str, Any]:
        return self.values

    async def invalidate(self) -> None:
        self.invalidated += 1


class FakeCatalog:
    def __init__(self) -> None:
        # (user_id, clinic_id) -> role
        self.members: dict[tuple[uuid.UUID, uuid.UUID], str] = {}
        self.calls = 0

    async def access(self, user_id: uuid.UUID, *, clinic_id: uuid.UUID) -> dict[str, Any]:
        self.calls += 1
        role = self.members.get((user_id, clinic_id))
        return {"allowed": role is not None, "role": role, "clinic_ids": [str(clinic_id)] if role else []}


class FakeDeps:
    def __init__(self) -> None:
        self.catalog = FakeCatalog()
        self.settings = FakeSettings()

    async def aclose(self) -> None:
        return None
