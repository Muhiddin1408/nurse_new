"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har daqiqa: `booking.created` kelmagan (bron yaratilmay qolgan) promo/paket band qilishlarini bo'shatish
  (ADR-0011: saga kompensatsiyasi yo'qolsa ham limit abadiy band qolmaydi). SKIP LOCKED — bir nechta nusxa xavfsiz.
"""

from __future__ import annotations

import asyncio

from medbron_common.logging import configure_logging
from medbron_common.workers import Every, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.services.quotes import sweep_stale_reservations


async def sweep() -> int:
    total = 0
    while True:
        async with get_session_factory()() as session:
            released = await sweep_stale_reservations(session)
        total += released
        if released < 500:
            return total


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    try:
        await run_periodic(stop, [PeriodicJob("reservations-sweep", Every(60), sweep, run_on_start=True)])
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
