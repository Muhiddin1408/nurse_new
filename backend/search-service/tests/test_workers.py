"""Worker kirish nuqtalari haqiqiy Postgres + RabbitMQ bilan: relay outbox'ni chiqaradi, consumer
handlerlarini ro'yxatdan o'tkazib ishga tushadi va SIGTERM (stop) da toza to'xtaydi.

Docker talab qiladi (testcontainers): `pytest -m integration`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration


def _reset_caches() -> None:
    from app.clients.elastic import get_es
    from app.core.config import get_settings
    from app.core.database import get_engine, get_session_factory
    from app.events import get_consumer

    for fn in (get_consumer, get_session_factory, get_engine, get_es, get_settings):
        fn.cache_clear()


@pytest.fixture
def worker_env(postgres_url: str, rabbitmq_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    monkeypatch.setenv("RABBITMQ_URL", rabbitmq_url)
    monkeypatch.setenv("WORKER_METRICS_PORT", "0")
    _reset_caches()
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = postgres_url
    command.upgrade(cfg, "head")
    yield
    _reset_caches()


async def _stop_after(stop: asyncio.Event, seconds: float) -> None:
    await asyncio.sleep(seconds)
    stop.set()


def test_event_consumer_starts_and_stops(worker_env: None, es_env: str) -> None:
    from app.workers import event_consumer

    async def scenario() -> None:
        stop = asyncio.Event()
        await asyncio.wait_for(asyncio.gather(event_consumer.main(stop), _stop_after(stop, 1.5)), timeout=30)

    asyncio.run(scenario())


def test_scheduler_starts_and_stops(worker_env: None, es_env: str) -> None:
    """catalog yo'q — mutaxassisliklar sinxroni xato beradi (log), scheduler yiqilmaydi."""
    from app.workers import scheduler

    async def scenario() -> None:
        stop = asyncio.Event()
        await asyncio.wait_for(asyncio.gather(scheduler.main(stop), _stop_after(stop, 1.5)), timeout=30)

    asyncio.run(scenario())
