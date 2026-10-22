"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator

from medbron_common.config import ServiceSettings
from medbron_common.sms import SmsConfig


class Settings(ServiceSettings):
    service_name: str = "notification-service"

    # --- kanallar ---
    sms_provider: Literal["console", "eskiz"] = "console"
    eskiz_base_url: str = "https://notify.eskiz.uz"
    eskiz_email: str | None = None
    eskiz_password: SecretStr | None = None
    eskiz_from: str = "4546"
    sms_timeout_seconds: float = 5.0

    # Push — faqat FCM (Android va iOS ikkalasi FCM orqali; APNs to'g'ridan-to'g'ri ishlatilmaydi)
    push_provider: Literal["console", "fcm", "off"] = "console"
    fcm_project_id: str | None = None
    # Service account JSON: fayl yoki base64 (`FCM_CREDENTIALS_JSON`, compose
    # `secrets/private/notification-service.env` dan)
    fcm_credentials_file: Path | None = None
    fcm_credentials_json: SecretStr | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str = "medbron_bot"
    # setWebhook(secret_token=...) bilan bir xil — webhook so'rovi shu bilan tekshiriladi (fail-closed)
    telegram_webhook_secret: SecretStr | None = None
    # Bog'lash havolasi tokeni (HMAC) uchun kalit
    telegram_link_secret: SecretStr | None = None
    telegram_link_ttl_seconds: int = 900
    # Ops ogohlantirishlari (refund yiqildi, nizo SLA, reconciliation...) — bo'sh bo'lsa o'chiq
    ops_telegram_chat_id: int | None = None

    # --- eslatmalar (monolit BOOKING_REMINDERS; 0 — o'chiq) ---
    reminder_first_hours: int = 24
    reminder_second_hours: int = 2
    review_request_after_hours: int = 2
    digest_hour: int = 7

    # --- dispatcher ---
    dispatch_batch_size: int = 100
    dispatch_interval_seconds: float = 2.0
    dispatch_max_attempts: int = 5
    template_cache_seconds: int = 60

    @model_validator(mode="after")
    def _channel_requirements(self) -> Settings:
        if self.environment == "prod":
            if self.sms_provider != "eskiz":
                raise ValueError("prod muhitida SMS_PROVIDER=eskiz majburiy")
            if self.push_provider == "console":
                raise ValueError("prod muhitida PUSH_PROVIDER=console taqiqlangan (fcm yoki off)")
        if self.push_provider == "fcm" and self.fcm_credentials_file is None and self.fcm_credentials_json is None:
            raise ValueError("PUSH_PROVIDER=fcm uchun FCM_CREDENTIALS_JSON yoki FCM_CREDENTIALS_FILE majburiy")
        if self.environment not in ("local", "test") and self.telegram_link_secret is None:
            raise ValueError("TELEGRAM_LINK_SECRET majburiy (local/test dan tashqari)")
        return self

    @property
    def link_secret(self) -> bytes:
        if self.telegram_link_secret is None:
            return b"local-only-telegram-link-secret"
        return self.telegram_link_secret.get_secret_value().encode()

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
