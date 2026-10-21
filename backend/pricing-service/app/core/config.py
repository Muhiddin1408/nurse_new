"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "pricing-service"

    catalog_service_url: str = "http://catalog-service:8080"
    admin_service_url: str = "http://admin-service:8080"

    # Quote Redis'da shuncha yashaydi (booking shu oraliqda commit qiladi)
    quote_ttl_seconds: int = 600
    # commit qilingan, lekin `booking.created` kelmagan band qilish shuncha daqiqadan keyin bo'shaydi
    reservation_ttl_minutes: int = 15
    # Takroriy tashrif chegirmasi oynasi (monolit FOLLOW_UP_WINDOW)
    follow_up_window_days: int = 14
    # Shifokor sozlamalari keshi (catalog bookability)
    doctor_cache_seconds: int = 60


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
