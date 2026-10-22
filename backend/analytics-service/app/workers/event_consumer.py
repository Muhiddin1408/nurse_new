"""Ingest worker — alohida konteyner: `python -m app.workers.event_consumer` (navbat `#` → ClickHouse)."""

from __future__ import annotations

import asyncio

from medbron_common.logging import configure_logging
from medbron_common.workers import run_worker

from app.clients.clickhouse import ClickHouse
from app.core.config import get_settings
from app.services.ingest import Ingestor


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-event-consumer", settings.log_level)
    if settings.rabbitmq_url is None:
        raise RuntimeError("RABBITMQ_URL o'rnatilmagan")
    ch = ClickHouse.from_settings(settings)
    try:
        await Ingestor(
            ch,
            amqp_url=settings.rabbitmq_url.get_secret_value(),
            batch_size=settings.ingest_batch_size,
            flush_seconds=settings.ingest_flush_seconds,
            prefetch=settings.ingest_prefetch,
            max_backoff=settings.ingest_max_backoff_seconds,
        ).run(stop)
    finally:
        await ch.aclose()


if __name__ == "__main__":
    run_worker(main)
