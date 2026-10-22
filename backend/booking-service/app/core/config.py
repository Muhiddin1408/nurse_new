"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan).

Biznes qoidalari (bekor qilish siyosati, hold TTL, lead time, waitlist) — admin-service global
sozlamalaridan (`PlatformSettingsClient`). Bu yerda faqat texnik parametrlar."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "booking-service"

    catalog_service_url: str = "http://catalog-service:8080"
    schedule_service_url: str = "http://schedule-service:8080"
    pricing_service_url: str = "http://pricing-service:8080"
    user_service_url: str = "http://user-service:8080"
    admin_service_url: str = "http://admin-service:8080"

    max_pending_bookings_per_client: int = 3
    max_reschedules: int = 2
    # Shifokor qabulni boshlashi mumkin bo'lgan eng erta vaqt (start_at dan oldin)
    start_window_minutes: int = 15
    # Tasdiqlangan qabul shuncha soatdan keyin avtomatik yakunlanadi (shifokor belgilamasa)
    auto_complete_after_hours: int = 24
    # To'lov ochilganda hold uzaytiriladi (monolit B3: PAYMENT_HOLD_EXTENSION_MINUTES)
    payment_extension_minutes: int = 15
    waitlist_max_notifications: int = 3
    waitlist_notify_cooldown_minutes: int = 15


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
