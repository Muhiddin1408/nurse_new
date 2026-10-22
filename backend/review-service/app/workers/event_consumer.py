"""Event consumer — alohida konteyner: `python -m app.workers.event_consumer` (BLUEPRINT 8.5)."""

from __future__ import annotations

import asyncio
import logging

from medbron_common.logging import configure_logging
from medbron_common.workers import run_worker

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.redis import get_redis
from app.events import get_consumer
from app.services.deps import Deps
from app.workers import handlers

logger = logging.getLogger(__name__)


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-event-consumer", settings.log_level)
    consumer = get_consumer()
    redis = get_redis() if settings.redis_url else None
    deps = Deps.create(redis)
    handlers.register(consumer, deps)
    try:
        if not consumer.routing_keys:
            # Servis hech narsa iste'mol qilmaydi — konteyner tinch kutadi (restart sikliga tushmaydi).
            # Kerak bo'lmasa docker-compose.yml dan event-consumer ni olib tashlang.
            logger.warning("no event handlers registered; consumer idle")
            await stop.wait()
            return
        await consumer.run(stop)
    finally:
        await deps.aclose()
        if redis is not None:
            await redis.aclose()
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
