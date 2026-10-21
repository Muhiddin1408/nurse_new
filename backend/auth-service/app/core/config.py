"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator

from medbron_common.config import ServiceSettings
from medbron_common.sms import SmsConfig


class Settings(ServiceSettings):
    service_name: str = "auth-service"

    # Imzo kaliti FAQAT shu servisning API konteyneriga: `JWT_PRIVATE_KEY` (base64 PEM, compose
    # `secrets/private/auth-service.env` dan) yoki fayl (hostdan ishga tushirishda).
    jwt_private_key: SecretStr | None = None
    jwt_private_key_path: Path = Path("/secrets-private/jwt-private.pem")
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_days: int = 30

    # OTP (monolit bilan bir xil chegaralar — A14)
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    otp_max_per_phone_hour: int = 5
    otp_max_per_ip_hour: int = 20
    otp_max_failed_verify_per_phone_hour: int = 10
    # Kod HMAC(otp_hash_secret, telefon:kod) ko'rinishida saqlanadi: baza sizib chiqsa ham
    # 10^6 variantni siz bilmasdan sinab bo'lmaydi.
    otp_hash_secret: SecretStr | None = None
    # Faqat lokal/dev + console SMS: kod javobga qo'shiladi (frontend va e2e testlar uchun).
    otp_debug_echo: bool = False

    sms_provider: Literal["console", "eskiz"] = "console"
    eskiz_base_url: str = "https://notify.eskiz.uz"
    eskiz_email: str | None = None
    eskiz_password: SecretStr | None = None
    eskiz_from: str = "4546"
    sms_timeout_seconds: float = 3.0

    @model_validator(mode="after")
    def _auth_requirements(self) -> Settings:
        if self.environment not in ("local", "test") and self.otp_hash_secret is None:
            raise ValueError("OTP_HASH_SECRET majburiy (local/test dan tashqari)")
        if self.otp_debug_echo and (self.sms_provider != "console" or self.environment == "prod"):
            raise ValueError("OTP_DEBUG_ECHO faqat SMS_PROVIDER=console va prod bo'lmagan muhitda")
        if self.environment == "prod" and self.sms_provider != "eskiz":
            raise ValueError("prod muhitida SMS_PROVIDER=eskiz majburiy")
        return self

    @property
    def otp_secret(self) -> bytes:
        if self.otp_hash_secret is None:
            return b"local-only-otp-secret"
        return self.otp_hash_secret.get_secret_value().encode()

    def sms_config(self) -> SmsConfig:
        return SmsConfig(
            provider=self.sms_provider,
            eskiz_base_url=self.eskiz_base_url,
            eskiz_email=self.eskiz_email,
            eskiz_password=self.eskiz_password,
            eskiz_from=self.eskiz_from,
            timeout_seconds=self.sms_timeout_seconds,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
