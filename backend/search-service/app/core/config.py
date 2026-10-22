"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "search-service"

    # Faqat reindex va mutaxassisliklar lug'ati uchun (2026-10-21 qarori) — oddiy rejimda eventlar
    catalog_service_url: str = "http://catalog-service:8080"

    elasticsearch_url: str = "http://search-service-elasticsearch:9200"
    elasticsearch_username: str | None = None
    elasticsearch_password: SecretStr | None = None
    elasticsearch_timeout_seconds: float = 5.0
    # Indeks/alias nomlari prefiksi (bitta klasterda bir nechta muhit yoki testlar)
    index_prefix: str = ""

    reindex_page_size: int = 200
    # `running` holatida shuncha vaqtdan ko'p turgan reindex yiqilgan hisoblanadi (yangisiga ruxsat)
    reindex_stale_minutes: int = 60
    # Ommaviy qidiruv javobi Redis keshi (ES'ni to'lqinlardan himoya); 0 — o'chiq
    search_cache_seconds: int = 15


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
