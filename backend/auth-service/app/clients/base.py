"""Boshqa servislarga HTTP clientlar shu yerdan meros oladi (BLUEPRINT 7.2).

    class CatalogClient(BaseClient):
        target = "catalog-service"

        async def bookability(self, doctor_id: UUID) -> Bookability:
            data = await self.http.get_json(f"/internal/v1/doctors/{doctor_id}/bookability")
            return Bookability.model_validate(data)

`clients/` faqat `services/` dan chaqiriladi (qatlam qoidasi). Har target — bitta ServiceClient
(connection pool), lifespan'da yaratiladi va yopiladi.
"""

from __future__ import annotations

from typing import ClassVar

from medbron_common.http_client import ServiceClient

from app.core.config import get_settings


class BaseClient:
    target: ClassVar[str]

    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        token = settings.internal_token.get_secret_value() if settings.internal_token else None
        self.http = ServiceClient(
            self.target,
            base_url or f"http://{self.target}:8080",
            caller=settings.service_name,
            token=token,
        )

    async def aclose(self) -> None:
        await self.http.aclose()
