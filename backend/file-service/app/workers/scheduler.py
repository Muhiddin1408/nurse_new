"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har soat: 24 soatda complete/attach qilinmagan fayllar (obyekt + thumbnail) o'chiriladi.
SKIP LOCKED — bir nechta nusxa parallel ishlasa ham bitta fayl ikki marta olinmaydi.
"""

from __future__ import annotations

import asyncio

from medbron_common.logging import configure_logging
from medbron_common.workers import Every, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.core.storage import get_storage
from app.services.files import purge_unattached


async def cleanup() -> int:
    total = 0
    while True:
        async with get_session_factory()() as session:
            purged = await purge_unattached(session, get_storage())
        total += purged
        if purged < 500:
            return total


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    try:
        await run_periodic(stop, [PeriodicJob("files-cleanup", Every(3600), cleanup, run_on_start=True)])
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
