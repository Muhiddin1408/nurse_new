"""catalog-service eksporti (contracts/openapi/catalog-service.internal.yaml, `/internal/v1/export/*`).
Faqat reindex va mutaxassisliklar lug'ati sinxroni chaqiradi — oddiy qidiruv yo'lida sinxron chaqiruv YO'Q."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, ClassVar
from uuid import UUID

from app.clients.base import BaseClient
from app.core.config import get_settings


class CatalogClient(BaseClient):
    target: ClassVar[str] = "catalog-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().catalog_service_url)

    async def _pages(self, path: str, page_size: int) -> AsyncIterator[list[dict[str, Any]]]:
        after: UUID | str | None = None
        while True:
            params: dict[str, Any] = {"limit": page_size}
            if after:
                params["after"] = str(after)
            page = await self.http.get_json(path, params=params)
            yield list(page["items"])
            after = page.get("next_after")
            if not after:
                return

    def doctors(self, page_size: int) -> AsyncIterator[list[dict[str, Any]]]:
        return self._pages("/internal/v1/export/doctors", page_size)

    def clinics(self, page_size: int) -> AsyncIterator[list[dict[str, Any]]]:
        return self._pages("/internal/v1/export/clinics", page_size)

    async def specializations(self) -> list[dict[str, Any]]:
        data = await self.http.get_json("/internal/v1/export/specializations")
        return list(data["items"])
