"""Upstream client'lar to'plami — API lifespan'da va worker'larda bir marta yaratiladi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from medbron_common.settings_client import PlatformSettingsClient

from app.clients.upstream import AdminClient, BookingClient, FileClient


@dataclass
class Deps:
    booking: BookingClient
    files: FileClient
    admin: AdminClient
    settings: PlatformSettingsClient

    @classmethod
    def create(cls, redis: Any | None) -> Deps:
        admin = AdminClient()
        return cls(
            booking=BookingClient(), files=FileClient(), admin=admin, settings=PlatformSettingsClient(admin.http, redis)
        )

    async def aclose(self) -> None:
        for client in (self.booking, self.files, self.admin):
            await client.aclose()
