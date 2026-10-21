"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "admin-service"

    # Admin JWT imzo kaliti (user kalitidan ALOHIDA, ADR-0005) — faqat API konteyneriga:
    # `ADMIN_JWT_PRIVATE_KEY` (base64 PEM, compose `secrets/private/admin-service.env` dan) yoki fayl.
    admin_jwt_private_key: SecretStr | None = None
    admin_jwt_private_key_path: Path = Path("/secrets-private/admin-jwt-private.pem")
    admin_access_token_ttl_seconds: int = 1800

    # TOTP sirlari bazada shifrlangan (Fernet). Generatsiya:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    totp_encryption_key: SecretStr | None = None
    totp_issuer: str = "MedBron Admin"

    # Login himoyasi (Redis)
    login_max_failures: int = 5
    login_lockout_seconds: int = 900
    login_max_per_ip_hour: int = 30
    mfa_token_ttl_seconds: int = 300
    mfa_max_attempts: int = 5
    password_min_length: int = 12

    settings_cache_max_age_seconds: int = 60

    @model_validator(mode="after")
    def _admin_requirements(self) -> Settings:
        if self.environment not in ("local", "test") and self.totp_encryption_key is None:
            raise ValueError("TOTP_ENCRYPTION_KEY majburiy (local/test dan tashqari)")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
