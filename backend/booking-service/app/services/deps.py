"""Upstream client'lar to'plami — API lifespan'da va worker'larda bir marta yaratiladi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from medbron_common.settings_client import PlatformSettingsClient

from app.clients.upstream import AdminClient, CatalogClient, PricingClient, ScheduleClient, UserClient


@dataclass
class Deps:
    catalog: CatalogClient
    schedule: ScheduleClient
    pricing: PricingClient
    users: UserClient
    admin: AdminClient
    settings: PlatformSettingsClient

    @classmethod
    def create(cls, redis: Any | None) -> Deps:
        admin = AdminClient()
        return cls(
            catalog=CatalogClient(),
            schedule=ScheduleClient(),
            pricing=PricingClient(),
            users=UserClient(),
            admin=admin,
            settings=PlatformSettingsClient(admin.http, redis),
        )

    async def aclose(self) -> None:
        for client in (self.catalog, self.schedule, self.pricing, self.users, self.admin):
            await client.aclose()
