"""Outbox relay — alohida konteyner: `python -m app.workers.outbox_relay` (BLUEPRINT 8.4)."""

from __future__ import annotations

import asyncio

from medbron_common.eventbus import EventPublisher, OutboxRelay
from medbron_common.logging import configure_logging
from medbron_common.workers import run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.models import OutboxEvent


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-outbox-relay", settings.log_level)
    if settings.rabbitmq_url is None:
        raise RuntimeError("RABBITMQ_URL o'rnatilmagan")
    publisher = EventPublisher(settings.rabbitmq_url.get_secret_value())
    try:
        await OutboxRelay(get_session_factory(), OutboxEvent, publisher).run(stop)
    finally:
        await publisher.close()
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
