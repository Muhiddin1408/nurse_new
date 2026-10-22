"""Global platforma sozlamalari (admin-service `GET /internal/v1/settings`) — o'qish tomoni.

    settings = PlatformSettingsClient(http_client_to_admin, redis)
    hold = (await settings.get())["schedule"]["hold_ttl_minutes"]

- Kesh Redis'da 60 s (servisning barcha konteynerlari uchun BITTA), shuning uchun event-consumer'dagi
  `admin.settings_changed` handleri `invalidate()` qilsa — API konteyneri ham darhol yangisini oladi.
- `If-None-Match` (ETag `"v<N>"`) — o'zgarmagan bo'lsa 304, JSON qayta uzatilmaydi.
- admin-service ishlamasa — oxirgi ma'lum qiymat (Redis'da muddatsiz) qaytariladi; u ham bo'lmasa
  `UpstreamUnavailable` (503). Standart qiymatlar shu yerda TAKRORLANMAYDI — egasi admin-service.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from medbron_common.http_client import ServiceClient, UpstreamError, UpstreamUnavailable

logger = logging.getLogger(__name__)

CACHE_KEY = "admin:settings:v1"
LAST_KNOWN_KEY = "admin:settings:v1:last"


class PlatformSettingsClient:
    def __init__(self, http: ServiceClient, redis: Any | None = None, *, ttl_seconds: int = 60) -> None:
        self._http = http
        self._redis = redis
        self._ttl = ttl_seconds
        self._local: dict[str, Any] | None = None  # Redis'siz (testlar) — jarayon ichida

    async def _cached(self, key: str) -> dict[str, Any] | None:
        if self._redis is None:
            return self._local if key == LAST_KNOWN_KEY else None
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def _store(self, snapshot: dict[str, Any]) -> None:
        self._local = snapshot
        if self._redis is not None:
            payload = json.dumps(snapshot)
            await self._redis.set(CACHE_KEY, payload, ex=self._ttl)
            await self._redis.set(LAST_KNOWN_KEY, payload)

    async def snapshot(self) -> dict[str, Any]:
        """`{"version": N, "values": {...}}`"""
        fresh = await self._cached(CACHE_KEY)
        if fresh is not None:
            return fresh
        last = await self._cached(LAST_KNOWN_KEY)
        headers = {"If-None-Match": f'"v{last["version"]}"'} if last else None
        try:
            response = await self._http.request("GET", "/internal/v1/settings", headers=headers, raise_for_status=False)
        except UpstreamUnavailable:
            if last is not None:
                logger.warning("admin settings unavailable; using last known", extra={"version": last["version"]})
                return last
            raise
        if response.status_code == 304 and last is not None:
            await self._store(last)
            return last
        if response.status_code >= 400:
            if last is not None:
                return last
            raise UpstreamError("admin-service", response)
        snapshot = response.json()
        await self._store(snapshot)
        return dict(snapshot)

    async def get(self) -> dict[str, Any]:
        values: dict[str, Any] = (await self.snapshot())["values"]
        return values

    async def invalidate(self) -> None:
        """`admin.settings_changed` handleridan. Oxirgi ma'lum qiymat qoladi (zaxira).
        Redis'siz rejimda kesh yo'q (har chaqiruv ETag bilan so'raydi) — tozalash shart emas."""
        if self._redis is not None:
            await self._redis.delete(CACHE_KEY)
