"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har daqiqa: to'lov muddati o'tgan bronlar → `expired` (schedule slotni `booking.expired` dan bo'shatadi);
- har soat: 24 soat oldin tugagan tasdiqlangan qabullar → `completed` (shifokor belgilamagan bo'lsa);
- kunlik: muddati o'tgan waitlist yozuvlari, 7 kundan eski idempotency kalitlari.
SKIP LOCKED — bir nechta nusxa parallel ishlashi xavfsiz.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from medbron_common.db import transaction
from medbron_common.logging import configure_logging
from medbron_common.workers import Every, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.repositories import bookings as repo
from app.services import lifecycle
from app.services.policy import LOCAL_TZ


async def expire_unpaid() -> int:
    total = 0
    while True:
        async with get_session_factory()() as session:
            done = await lifecycle.expire_due(session)
        total += done
        if done < 500:
            return total


async def auto_complete() -> int:
    total = 0
    while True:
        async with get_session_factory()() as session:
            done = await lifecycle.auto_complete(session)
        total += done
        if done < 500:
            return total


async def housekeeping() -> dict[str, int]:
    async with get_session_factory()() as session, transaction(session):
        waitlist = await repo.expire_waitlist(session, datetime.now(LOCAL_TZ).date())
        keys = await repo.purge_keys(session, datetime.now(UTC) - timedelta(days=7))
    return {"waitlist_expired": waitlist, "idempotency_keys": keys}


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    try:
        await run_periodic(
            stop,
            [
                PeriodicJob("bookings-expire", Every(60), expire_unpaid, run_on_start=True),
                PeriodicJob("bookings-auto-complete", Every(3600), auto_complete, run_on_start=True),
                PeriodicJob("bookings-housekeeping", Every(24 * 3600), housekeeping, run_on_start=True),
            ],
        )
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
