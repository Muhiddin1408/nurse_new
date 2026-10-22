"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "analytics-service"

    # ClickHouse HTTP interfeysi (servisning O'Z konteyneri, ichki tarmoq)
    clickhouse_url: str = "http://analytics-service-clickhouse:8123"
    clickhouse_user: str = "analytics"
    clickhouse_password: SecretStr | None = None
    clickhouse_database: str = "analytics"
    clickhouse_timeout_seconds: float = 30.0

    # Ingest: navbat `#` ga bind; xabarlar to'plab ClickHouse'ga yoziladi (batch / vaqt — qaysi oldin)
    ingest_batch_size: int = 500
    ingest_flush_seconds: float = 2.0
    # prefetch ≥ batch: broker bir partiyani to'liq bera olsin
    ingest_prefetch: int = 1000
    # ClickHouse yiqilsa partiya qayta yoziladi: 1, 2, 4 … shu soniyagacha (xabarlar ack qilinmaydi)
    ingest_max_backoff_seconds: float = 30.0

    # Hisobotlar oralig'i (kun)
    report_max_days: int = 366


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
