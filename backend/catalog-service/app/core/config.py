"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "catalog-service"
    file_service_url: str = "http://file-service:8080"
    # Moderator uchun maxfiy hujjat havolasi (file-service ≤ 300 s)
    document_url_ttl_seconds: int = 300
    moderation_sla_hours: int = 48
    license_reminder_days: int = 30
    # Litsenziya tekshiruvi (Asia/Tashkent), har kuni
    license_check_hour: int = 9
    # Ommaviy reyting shu sondan kam sharhda yashiriladi (C11)
    min_reviews_for_rating: int = 5


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
