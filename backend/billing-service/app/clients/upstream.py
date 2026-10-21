"""Sinxron bog'liqliklar (reestr: catalog, admin) — contracts/openapi/*.internal.yaml."""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from app.clients.base import BaseClient
from app.core.config import get_settings


class CatalogClient(BaseClient):
    target: ClassVar[str] = "catalog-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().catalog_service_url)

    async def access(self, user_id: UUID, *, clinic_id: UUID) -> dict[str, Any]:
        """`{allowed, role, clinic_ids}` — shu user shu klinikani boshqaradimi."""
        params = {"user_id": str(user_id), "clinic_id": str(clinic_id)}
        data: dict[str, Any] = await self.http.get_json("/internal/v1/access", params=params)
        return data


class AdminClient(BaseClient):
    """Faqat global sozlamalar uchun (`PlatformSettingsClient` shu http client orqali)."""

    target: ClassVar[str] = "admin-service"

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(base_url or get_settings().admin_service_url)
