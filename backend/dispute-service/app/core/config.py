"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan).

Biznes qoidasi (nizo SLA soati) — admin-service global sozlamasidan (`sla.dispute_hours`,
`PlatformSettingsClient`). Bu yerda faqat texnik parametrlar."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "dispute-service"

    booking_service_url: str = "http://booking-service:8080"
    file_service_url: str = "http://file-service:8080"
    admin_service_url: str = "http://admin-service:8080"

    # Bitta nizoga biriktiriladigan dalillar (fayllar) chegarasi
    max_evidence_files: int = 10
    # SLA buzilishini tekshirish oralig'i, soniya (BLUEPRINT: har 15 daqiqa)
    sla_check_interval_seconds: int = 900
    # Admin ko'rinishidagi dalil havolasi muddati (file-service presigned URL, ≤ 300)
    evidence_url_ttl_seconds: int = 300


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
