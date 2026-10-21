"""Davriy vazifalar — alohida konteyner: `python -m app.workers.scheduler`.

- har kuni 04:00 (Asia/Tashkent): bugun admin sozlamasidagi payout kuni (`payout.weekday`, 0 — dushanba)
  bo'lsa — o'tgan to'liq hafta uchun payout hisoblanadi. Idempotent (UNIQUE + SKIP LOCKED) — bir nechta
  nusxa yoki qayta ishga tushish dublikat bermaydi.
"""

from __future__ import annotations

import asyncio

from medbron_common.logging import configure_logging
from medbron_common.workers import DailyAt, PeriodicJob, run_periodic, run_worker

from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.core.redis import get_redis
from app.services import payouts
from app.services.deps import Deps


async def weekly_payouts(deps: Deps) -> int:
    weekday = int((await deps.settings.get())["payout"]["weekday"])
    if payouts.local_today().weekday() != weekday:
        return 0
    async with get_session_factory()() as session:
        return len(await payouts.build(session, deps))


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-scheduler", settings.log_level)
    redis = get_redis() if settings.redis_url else None
    deps = Deps.create(redis)
    try:
        await run_periodic(
            stop,
            [
                PeriodicJob(
                    "payouts-weekly",
                    DailyAt(settings.payout_build_hour, 0, "Asia/Tashkent"),
                    lambda: weekly_payouts(deps),
                ),
            ],
        )
    finally:
        await deps.aclose()
        if redis is not None:
            await redis.aclose()
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
