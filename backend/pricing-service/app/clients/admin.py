"""admin-service: global sozlamalar (`medbron_common.settings_client`, Redis'da 60 s, ETag)."""

from __future__ import annotations

from typing import Any, ClassVar

from medbron_common.settings_client import PlatformSettingsClient

from app.clients.base import BaseClient
from app.core.config import get_settings


class AdminClient(BaseClient):
    target: ClassVar[str] = "admin-service"

    def __init__(self, base_url: str | None = None, redis: Any | None = None) -> None:
        super().__init__(base_url or get_settings().admin_service_url)
        self.settings = PlatformSettingsClient(self.http, redis)
