"""Upstream va provayder client'lari to'plami — API lifespan'da va worker'larda bir marta yaratiladi."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from medbron_common.settings_client import PlatformSettingsClient

from app.clients.providers import ClickProvider, PaymeProvider, ProviderGateway
from app.clients.upstream import AdminClient, BookingClient
from app.services import journal


@dataclass
class Deps:
    booking: BookingClient
    admin: AdminClient
    settings: PlatformSettingsClient
    payme: PaymeProvider
    click: ClickProvider

    @classmethod
    def create(cls, redis: Any | None) -> Deps:
        admin = AdminClient()
        return cls(
            booking=BookingClient(),
            admin=admin,
            settings=PlatformSettingsClient(admin.http, redis),
            payme=PaymeProvider(ProviderGateway("payme", journal=journal.outbound)),
            click=ClickProvider(ProviderGateway("click", journal=journal.outbound)),
        )

    def provider(self, name: str) -> PaymeProvider | ClickProvider:
        return self.payme if name == "payme" else self.click

    async def aclose(self) -> None:
        for client in (self.booking, self.admin):
            await client.aclose()
        for gateway in (self.payme.gateway, self.click.gateway):
            await gateway.aclose()
