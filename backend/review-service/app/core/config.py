"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan).

Biznes qoidalari (sharh oynasi `sla.review_window_days`) — admin-service sozlamalaridan. Bu yerda texnik parametrlar."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "review-service"

    booking_service_url: str = "http://booking-service:8080"
    admin_service_url: str = "http://admin-service:8080"

    # Reyting shuncha published sharhdan boshlab ko'rsatiladi (monolit MIN_REVIEWS_FOR_RATING)
    min_reviews_for_rating: int = 5
    max_comment_length: int = 2000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
