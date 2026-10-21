"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "__SERVICE_NAME__"
    # Servisga xos sozlamalarni shu yerga qo'shing (va .env.example ga ham!).


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
