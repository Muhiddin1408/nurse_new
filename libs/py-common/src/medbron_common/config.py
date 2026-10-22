"""Servis sozlamalari uchun asos klass.

Har servis o'z `Settings(ServiceSettings)` ini e'lon qiladi va `get_settings()` ni
`lru_cache` bilan o'raydi. Maydonlar muhit o'zgaruvchilaridan (katta harf bilan) o'qiladi.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INTERNAL_TOKEN_PREFIX = "INTERNAL_TOKEN_"  # noqa: S105 — o'zgaruvchi nomi prefiksi, sir emas


class ServiceSettings(BaseSettings):
    # env_ignore_empty: `.env` dagi `OPS_TELEGRAM_CHAT_ID=` kabi bo'sh qiymat — "o'rnatilmagan" (standart),
    # bo'sh satr emas (aks holda `int | None` / `Path | None` maydonlarida servis ishga tushmaydi).
    # MEDBRON_ENV_FILE="" — `.env` o'qilmaydi (testlar dev `.env` dan ajratiladi).
    model_config = SettingsConfigDict(
        env_file=os.environ.get("MEDBRON_ENV_FILE", ".env") or None,
        extra="ignore",
        frozen=True,
        env_ignore_empty=True,
    )

    service_name: str
    environment: Literal["local", "test", "dev", "prod"] = "local"
    log_level: str = "INFO"

    # BLUEPRINT 9: Docker ichida DATABASE_URL_DOCKER ustun keladi.
    database_url: str | None = None
    database_url_docker: str | None = None
    db_pool_size: int = 10
    db_max_overflow: int = 5

    redis_url: str | None = None
    rabbitmq_url: SecretStr | None = None
    consumer_prefetch: int = 20

    jwt_public_key_path: Path = Path("/secrets/jwt-public.pem")
    admin_jwt_public_key_path: Path = Path("/secrets/admin-jwt-public.pem")
    jwt_issuer: str = "medbron-auth"
    jwt_audience: str = "medbron"
    admin_jwt_issuer: str = "medbron-admin"
    admin_jwt_audience: str = "admin"

    # Shu servis BOSHQA servisga murojaat qilganda ko'rsatadigan token.
    internal_token: SecretStr | None = None

    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: SecretStr | None = None

    @model_validator(mode="after")
    def _prod_requirements(self) -> ServiceSettings:
        if self.environment == "prod" and not self.effective_database_url:
            raise ValueError("prod muhitida DATABASE_URL majburiy")
        return self

    @property
    def effective_database_url(self) -> str | None:
        return self.database_url_docker or self.database_url

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    def inbound_internal_tokens(self) -> dict[str, SecretStr]:
        """Bu servisni chaqirishga ruxsat etilgan servislar tokenlari.

        Har chaqiruvchi uchun alohida o'zgaruvchi: `INTERNAL_TOKEN_BOOKING_SERVICE=...`
        → `{"booking-service": SecretStr(...)}` (BLUEPRINT 6.4).
        """
        env_file = self.model_config.get("env_file")
        values: dict[str, str | None] = {}
        if isinstance(env_file, str) and Path(env_file).is_file():
            values.update(dotenv_values(env_file))
        values.update(os.environ)
        tokens: dict[str, SecretStr] = {}
        for key, value in values.items():
            if key.upper().startswith(INTERNAL_TOKEN_PREFIX) and value:
                caller = key[len(INTERNAL_TOKEN_PREFIX) :].lower().replace("_", "-")
                tokens[caller] = SecretStr(value)
        return tokens
