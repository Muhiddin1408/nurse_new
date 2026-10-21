"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "user-service"
    file_service_url: str = "http://file-service:8080"
    max_patients_per_user: int = 20
    max_addresses_per_user: int = 20
    max_favorites_per_user: int = 200


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
