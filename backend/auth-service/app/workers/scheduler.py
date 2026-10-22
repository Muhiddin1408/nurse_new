"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har soat: 24 soatdan eski OTP kodlar va muddati o'tgan refresh tokenlar o'chiriladi.
Bir nechta nusxa parallel ishlasa ham xavfsiz (oddiy DELETE ... WHERE).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from medbron_common.db import transaction
from medbron_common.logging import configure_logging
from medbron_common.workers import Every, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.repositories import identity as repo

OTP_RETENTION = timedelta(days=1)


async def cleanup() -> dict[str, int]:
    now = datetime.now(UTC)
    async with get_session_factory()() as session, transaction(session):
        otps = await repo.purge_otps(session, now - OTP_RETENTION)
        tokens = await repo.purge_refresh_tokens(session, now)
    return {"otp_codes": otps, "refresh_tokens": tokens}


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    try:
        await run_periodic(stop, [PeriodicJob("auth-cleanup", Every(3600), cleanup, run_on_start=True)])
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
