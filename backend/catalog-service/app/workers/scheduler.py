"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har kuni 09:00 (Asia/Tashkent): litsenziya muddati o'tgan tasdiqlangan shifokorlar to'xtatiladi
  (`doctor.suspended`, sabab `license_expired`); 30 kun qolganlarga bir marta eslatma (`doctor.license_expiring`).
Bir nechta nusxa parallel ishlasa ham xavfsiz: o'tishlar qator qulfi ostida, eslatma — shartli UPDATE.
"""

from __future__ import annotations

import asyncio

from medbron_common.db import transaction
from medbron_common.logging import configure_logging
from medbron_common.workers import DailyAt, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.services.doctors import run_license_check


async def license_check() -> dict[str, int]:
    async with get_session_factory()() as session, transaction(session):
        return await run_license_check(session)


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    try:
        await run_periodic(
            stop,
            [PeriodicJob("license-check", DailyAt(settings.license_check_hour, 0, "Asia/Tashkent"), license_check)],
        )
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
