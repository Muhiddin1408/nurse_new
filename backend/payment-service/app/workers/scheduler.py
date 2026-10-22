"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har daqiqa: navbatdagi refundlar provayderga (retry, max urinish, manual_required);
- har 5 daqiqa: Click fiskal cheklari (provayder OFD'si);
- har 10 daqiqa: provayder muddati (12 soat) o'tgan ochiq to'lov urinishlari → expired;
- har kecha 03:00 (Toshkent): ichki reconciliation (ledger balansi, daftarga tushmagan to'lov, refundsiz needs_refund);
- har kecha 04:30: provayder jurnali retention'i.
SKIP LOCKED / UNIQUE — bir nechta nusxa parallel ishlashi xavfsiz.
"""

from __future__ import annotations

import asyncio

from medbron_common.logging import configure_logging
from medbron_common.workers import DailyAt, Every, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.core.redis import get_redis
from app.services import maintenance, reconciliation, refunds
from app.services.deps import Deps


async def reconcile_internal() -> int:
    async with get_session_factory()() as session, session.begin():
        return len(await reconciliation.reconcile_internal(session, reconciliation.yesterday()))


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    redis = get_redis() if settings.redis_url else None
    deps = Deps.create(redis)
    factory = get_session_factory()
    try:
        await run_periodic(
            stop,
            [
                PeriodicJob(
                    "refunds-process", Every(60), lambda: refunds.process_due(factory, deps), run_on_start=True
                ),
                PeriodicJob("click-fiscal", Every(300), lambda: maintenance.submit_click_fiscal(factory, deps)),
                PeriodicJob(
                    "payments-expire", Every(600), lambda: maintenance.expire_stale(factory), run_on_start=True
                ),
                PeriodicJob("reconcile-internal", DailyAt(3, 0), reconcile_internal),
                PeriodicJob("provider-logs-purge", DailyAt(4, 30), lambda: maintenance.purge_logs(factory)),
            ],
        )
    finally:
        await deps.aclose()
        if redis is not None:
            await redis.aclose()
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
