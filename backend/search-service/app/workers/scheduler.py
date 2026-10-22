"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har 5 s: navbatdagi reindex (`reindex_runs.pending`) — bir vaqtda bitta (UNIQUE + run qulfi);
- har kecha 03:30 (+ ishga tushganda): mutaxassisliklar lug'ati catalog eksportidan (nomlar eventlarda yo'q).
"""

from __future__ import annotations

import asyncio

from medbron_common.logging import configure_logging
from medbron_common.workers import DailyAt, Every, PeriodicJob, run_periodic, run_worker

from app.clients import elastic
from app.clients.catalog import CatalogClient
from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.services import reindex


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    catalog = CatalogClient()
    factory = get_session_factory()
    es = elastic.get_es()
    try:
        await run_periodic(
            stop,
            [
                PeriodicJob("search-reindex", Every(5), lambda: reindex.run_pending(factory, catalog)),
                PeriodicJob(
                    "search-specializations",
                    DailyAt(3, 30),
                    lambda: reindex.sync_specializations(factory, catalog),
                    run_on_start=True,
                ),
            ],
        )
    finally:
        await catalog.aclose()
        await es.close()
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
