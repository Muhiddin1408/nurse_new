"""Fon jarayonlari (outbox relay, event consumer, scheduler) uchun: SIGTERM/SIGINT bilan toza
to'xtash (BLUEPRINT 8.4, 5-qadam), Prometheus `/metrics` (WORKER_METRICS_PORT, standart 9100) va
davriy vazifalar (`run_periodic`).

    await run_periodic(stop, [
        PeriodicJob("otp-cleanup", Every(3600), cleanup_otps),
        PeriodicJob("doctor-digest", DailyAt(7, 0, "Asia/Tashkent"), send_digests),
    ])

Davriy vazifa bir nechta nusxada parallel ishlashi mumkin (deploy paytida ikki konteyner) —
shuning uchun vazifaning o'zi idempotent bo'lishi shart (SKIP LOCKED, UNIQUE, `ON CONFLICT`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from prometheus_client import Counter, Gauge, start_http_server

logger = logging.getLogger(__name__)

DEFAULT_METRICS_PORT = 9100


def install_stop_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover — Windows
            signal.signal(sig, lambda *_: stop.set())


def start_metrics_server(port: int | None = None) -> int | None:
    """`WORKER_METRICS_PORT=0` — o'chirilgan (masalan, bir hostda bir nechta worker testda)."""
    port = port if port is not None else int(os.environ.get("WORKER_METRICS_PORT", DEFAULT_METRICS_PORT))
    if port <= 0:
        return None
    start_http_server(port)
    logger.info("worker metrics listening", extra={"port": port})
    return port


def run_worker(main: Callable[[asyncio.Event], Awaitable[None]], *, metrics_port: int | None = None) -> None:
    """`python -m app.workers.outbox_relay` kabi kirish nuqtalari uchun."""

    async def runner() -> None:
        stop = asyncio.Event()
        install_stop_signals(stop)
        start_metrics_server(metrics_port)
        await main(stop)
        logger.info("worker stopped")

    asyncio.run(runner())


JOB_RUNS = Counter("periodic_job_runs_total", "Davriy vazifa ishga tushishlari", ["job", "result"])
JOB_LAST_SUCCESS = Gauge("periodic_job_last_success_timestamp", "Oxirgi muvaffaqiyatli ishga tushish (unix)", ["job"])


@dataclass(frozen=True)
class Every:
    seconds: float

    def next_run(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.seconds)


@dataclass(frozen=True)
class DailyAt:
    """Har kuni mahalliy vaqt bo'yicha (masalan, 07:00 Asia/Tashkent)."""

    hour: int
    minute: int = 0
    tz: str = "Asia/Tashkent"

    def next_run(self, now: datetime) -> datetime:
        local = now.astimezone(ZoneInfo(self.tz))
        candidate = local.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)


@dataclass(frozen=True)
class PeriodicJob:
    name: str
    schedule: Every | DailyAt
    run: Callable[[], Awaitable[object]]
    run_on_start: bool = False


async def _job_loop(job: PeriodicJob, stop: asyncio.Event, clock: Callable[[], datetime]) -> None:
    next_at = clock() if job.run_on_start else job.schedule.next_run(clock())
    while not stop.is_set():
        delay = (next_at - clock()).total_seconds()
        if delay > 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
        started = time.monotonic()
        try:
            result = await job.run()
        except Exception:
            # Bitta vazifa xatosi butun scheduler'ni yiqitmaydi; keyingi navbatda qayta uriniladi.
            JOB_RUNS.labels(job=job.name, result="error").inc()
            logger.exception("periodic job failed", extra={"job": job.name})
        else:
            JOB_RUNS.labels(job=job.name, result="ok").inc()
            JOB_LAST_SUCCESS.labels(job=job.name).set(time.time())
            logger.info(
                "periodic job done",
                extra={"job": job.name, "result": result, "duration_ms": int((time.monotonic() - started) * 1000)},
            )
        next_at = job.schedule.next_run(clock())


async def run_periodic(
    stop: asyncio.Event,
    jobs: Sequence[PeriodicJob],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Har vazifa o'z siklida; `stop` o'rnatilganda joriy ish tugashi kutiladi va qaytadi."""
    await asyncio.gather(*(_job_loop(job, stop, clock) for job in jobs))
