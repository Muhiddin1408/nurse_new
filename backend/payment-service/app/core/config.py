"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan).

Provayder kalitlari (Payme, Click) — faqat shu servisda. Bo'sh kalit — fail-closed: webhook imzosi
tekshirilmaydi deb hisoblanmaydi, so'rov RAD etiladi. Biznes sozlamalari (fiskal TIN) — admin-service."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, model_validator

from medbron_common.config import ServiceSettings


def _ips(raw: str) -> frozenset[str]:
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


class Settings(ServiceSettings):
    service_name: str = "payment-service"

    booking_service_url: str = "http://booking-service:8080"
    admin_service_url: str = "http://admin-service:8080"

    # --- Payme Merchant API (JSON-RPC webhook, Basic Auth `Paycom:<key>`) ---
    payme_merchant_id: str = ""
    payme_secret_key: SecretStr | None = None
    payme_checkout_url: str = "https://checkout.paycom.uz"
    # Vergul bilan; bo'sh — allowlist o'chiq (prod'da Kong ham tekshiradi)
    payme_allowed_ips: str = ""

    # --- Click SHOP API (prepare/complete webhook, MD5 imzo) + Merchant API (reversal, fiskal) ---
    click_service_id: str = ""
    click_merchant_id: str = ""
    click_merchant_user_id: str = ""
    click_secret_key: SecretStr | None = None
    click_api_url: str = "https://api.click.uz/v2/merchant"
    click_checkout_url: str = "https://my.click.uz/services/pay"
    click_allowed_ips: str = ""

    webhook_max_body_bytes: int = 64 * 1024
    # Provayderga chiquvchi so'rovlar (refund, fiskal)
    provider_connect_timeout: float = 3.0
    provider_read_timeout: float = 10.0
    refund_max_attempts: int = 5
    # Payme tranzaksiyasi 12 soatda eskiradi (protokol)
    payme_transaction_timeout_hours: int = 12
    # Nizoda dalil — provayder so'rov/javob jurnali shuncha saqlanadi
    provider_log_retention_days: int = 365

    @model_validator(mode="after")
    def _prod_provider_keys(self) -> Settings:
        """Prod — fail-closed: yarim sozlangan provayder yoki IP allowlist'siz webhook bilan ishga tushmaydi."""
        if not self.is_prod:
            return self
        providers = {
            "payme": {
                "PAYME_MERCHANT_ID": self.payme_merchant_id,
                "PAYME_SECRET_KEY": self.payme_secret_key and self.payme_secret_key.get_secret_value(),
                "PAYME_ALLOWED_IPS": self.payme_allowed_ips.strip(),
            },
            "click": {
                "CLICK_SERVICE_ID": self.click_service_id,
                "CLICK_MERCHANT_ID": self.click_merchant_id,
                "CLICK_MERCHANT_USER_ID": self.click_merchant_user_id,
                "CLICK_SECRET_KEY": self.click_secret_key and self.click_secret_key.get_secret_value(),
                "CLICK_ALLOWED_IPS": self.click_allowed_ips.strip(),
            },
        }
        enabled = 0
        for name, fields in providers.items():
            missing = [k for k, v in fields.items() if not v]
            if len(missing) == len(fields):
                continue
            if missing:
                raise ValueError(f"prod: {name} yarim sozlangan — yetishmaydi: {', '.join(missing)}")
            enabled += 1
        if not enabled:
            raise ValueError("prod: hech bir to'lov provayderi sozlanmagan (PAYME_* yoki CLICK_*)")
        return self

    @property
    def payme_ips(self) -> frozenset[str]:
        return _ips(self.payme_allowed_ips)

    @property
    def click_ips(self) -> frozenset[str]:
        return _ips(self.click_allowed_ips)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
