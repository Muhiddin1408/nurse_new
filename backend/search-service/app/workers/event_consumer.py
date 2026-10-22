"""Event consumer — alohida konteyner: `python -m app.workers.event_consumer` (BLUEPRINT 8.5)."""

from __future__ import annotations

import asyncio
import logging

from medbron_common.logging import configure_logging
from medbron_common.workers import run_worker

from app.clients import elastic
from app.core.config import get_settings
from app.core.database import get_engine
from app.events import get_consumer
from app.workers import handlers

logger = logging.getLogger(__name__)


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-event-consumer", settings.log_level)
    consumer = get_consumer()
    handlers.register(consumer)
    es = elastic.get_es()
    try:
        await elastic.ensure_indices(es)  # bo'sh klaster: doctors_v1/clinics_v1 + alias
        if not consumer.routing_keys:
            # Servis hech narsa iste'mol qilmaydi — konteyner tinch kutadi (restart sikliga tushmaydi).
            # Kerak bo'lmasa docker-compose.yml dan event-consumer ni olib tashlang.
            logger.warning("no event handlers registered; consumer idle")
            await stop.wait()
            return
        await consumer.run(stop)
    finally:
        await es.close()
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
