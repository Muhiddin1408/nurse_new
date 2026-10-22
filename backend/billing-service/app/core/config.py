"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan).

Biznes qoidalari (payout minimal summasi, payout kuni) — admin-service global sozlamalaridan
(`PlatformSettingsClient`, `payout` bo'limi). Bu yerda faqat texnik parametrlar."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "billing-service"

    catalog_service_url: str = "http://catalog-service:8080"
    admin_service_url: str = "http://admin-service:8080"

    # Haftalik payout hisoblash vaqti (Asia/Tashkent); kuni — admin sozlamasi `payout.weekday`
    payout_build_hour: int = 4
    # Klinika a'zoligi (catalog `/internal/v1/access`) keshi
    access_cache_seconds: int = 60


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
