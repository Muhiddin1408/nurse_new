"""catalog-service internal API (`contracts/openapi/catalog-service.internal.yaml`)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from medbron_common.http_client import UpstreamError

from app.clients.base import BaseClient
from app.core.config import get_settings


@dataclass(frozen=True)
class DoctorSettings:
    doctor_id: uuid.UUID
    bookable: bool
    clinic_payment_mode: str
    follow_up_discount_percent: int


class DoctorNotFound(Exception):
    pass


class CatalogClient(BaseClient):
    target: ClassVar[str] = "catalog-service"

    def __init__(self, base_url: str | None = None, redis: Any | None = None) -> None:
        super().__init__(base_url or get_settings().catalog_service_url)
        self._redis = redis

    async def doctor_settings(self, doctor_id: uuid.UUID) -> DoctorSettings:
        """Bookability — Redis'da `DOCTOR_CACHE_SECONDS` (60 s)."""
        key = f"catalog:bookability:{doctor_id}"
        data: dict[str, Any] | None = None
        if self._redis is not None:
            raw = await self._redis.get(key)
            data = json.loads(raw) if raw else None
        if data is None:
            try:
                data = await self.http.get_json(f"/internal/v1/doctors/{doctor_id}/bookability")
            except UpstreamError as exc:
                if exc.upstream_status == 404:
                    raise DoctorNotFound(str(doctor_id)) from exc
                raise
            if self._redis is not None:
                await self._redis.set(key, json.dumps(data), ex=get_settings().doctor_cache_seconds)
        assert data is not None
        return DoctorSettings(
            doctor_id=uuid.UUID(data["doctor_id"]),
            bookable=bool(data["bookable"]),
            clinic_payment_mode=str(data["clinic_payment_mode"]),
            follow_up_discount_percent=int(data["follow_up_discount_percent"]),
        )

    async def services(self, ids: Sequence[uuid.UUID], bookable_for: uuid.UUID | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {"ids": ",".join(str(i) for i in ids)}
        if bookable_for is not None:
            params["bookable_for"] = str(bookable_for)
        data = await self.http.get_json("/internal/v1/services", params=params)
        return list(data["items"])
