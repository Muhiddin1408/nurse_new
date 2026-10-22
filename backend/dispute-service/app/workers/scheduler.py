"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har 15 daqiqa (`SLA_CHECK_INTERVAL_SECONDS`): SLA muddati o'tgan ochiq nizolar → `dispute.sla_breached` (bir marta).
SKIP LOCKED — bir nechta nusxa parallel ishlashi xavfsiz.
"""

from __future__ import annotations

import asyncio

from medbron_common.logging import configure_logging
from medbron_common.workers import Every, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.services import disputes


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    factory = get_session_factory()
    try:
        await run_periodic(
            stop,
            [
                PeriodicJob(
                    "disputes-sla",
                    Every(settings.sla_check_interval_seconds),
                    lambda: disputes.check_sla(factory),
                    run_on_start=True,
                ),
            ],
        )
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
