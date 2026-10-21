"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan).

Provayder kalitlari (Payme, Click) — faqat shu servisda. Bo'sh kalit — fail-closed: webhook imzosi
tekshirilmaydi deb hisoblanmaydi, so'rov RAD etiladi. Biznes sozlamalari (fiskal TIN) — admin-service."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr

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

    @property
    def payme_ips(self) -> frozenset[str]:
        return _ips(self.payme_allowed_ips)

    @property
    def click_ips(self) -> frozenset[str]:
        return _ips(self.click_allowed_ips)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
