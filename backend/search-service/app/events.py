"""Event bus: faqat consumer (subscribe). search-service hech narsa publish qilmaydi (katalog) — outbox relay yo'q;
`outbox_events` jadvali shablon migratsiyasida qoladi (bo'sh)."""

from __future__ import annotations

from functools import lru_cache

from medbron_common.eventbus import EventConsumer

from app.core.config import get_settings
from app.core.database import get_session_factory
from app.models import ProcessedEvent


@lru_cache(maxsize=1)
def get_consumer() -> EventConsumer:
    settings = get_settings()
    if settings.rabbitmq_url is None:
        raise RuntimeError("RABBITMQ_URL o'rnatilmagan")
    return EventConsumer(
        service_name=settings.service_name,
        amqp_url=settings.rabbitmq_url.get_secret_value(),
        session_factory=get_session_factory(),
        processed_model=ProcessedEvent,
        prefetch=settings.consumer_prefetch,
    )
