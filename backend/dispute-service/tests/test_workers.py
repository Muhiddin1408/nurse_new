"""Worker kirish nuqtalari haqiqiy Postgres + RabbitMQ bilan: relay outbox'ni chiqaradi, consumer
handlerlarini ro'yxatdan o'tkazib ishga tushadi va SIGTERM (stop) da toza to'xtaydi.

Docker talab qiladi (testcontainers): `pytest -m integration`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from medbron_common.events import CATALOG

pytestmark = pytest.mark.integration


def _reset_caches() -> None:
    from app.core.config import get_settings
    from app.core.database import get_engine, get_session_factory
    from app.events import get_consumer

    for fn in (get_consumer, get_session_factory, get_engine, get_settings):
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
    # Boshqa testlardan qolgan outbox qatorlari relay natijasini buzmasin (sessiya bo'yi umumiy baza)
    from tests.conftest import Db

    Db(postgres_url).scalar("TRUNCATE outbox_events")
    yield
    _reset_caches()


async def _stop_after(stop: asyncio.Event, seconds: float) -> None:
    await asyncio.sleep(seconds)
    stop.set()


def test_outbox_relay_publishes_pending_events(worker_env: None) -> None:
    from medbron_common.eventbus import Outbox

    from app.core.database import get_session_factory
    from app.models import OutboxEvent
    from app.workers import outbox_relay

    publishable = [et for et, spec in CATALOG.items() if "dispute-service" in spec.publishers]
    if not publishable:
        pytest.skip("servis hech qanday event publish qilmaydi")
    # Relay payload ma'nosiga qaramaydi — sxema tekshiruvisiz namunaviy qator yetarli.
    raw_outbox = Outbox(OutboxEvent, producer="dispute-service", validate=False)

    async def scenario() -> list[str]:
        async with get_session_factory()() as session, session.begin():
            raw_outbox.add_event(session, publishable[0], aggregate_id=uuid.uuid4(), data={})
        stop = asyncio.Event()
        await asyncio.gather(outbox_relay.main(stop), _stop_after(stop, 2.0))
        _reset_caches()
        async with get_session_factory()() as session:
            return list((await session.scalars(select(OutboxEvent.status))).all())

    assert asyncio.run(scenario()) == ["published"]


def test_event_consumer_starts_and_stops(worker_env: None) -> None:
    from app.workers import event_consumer

    async def scenario() -> None:
        stop = asyncio.Event()
        await asyncio.wait_for(asyncio.gather(event_consumer.main(stop), _stop_after(stop, 1.5)), timeout=30)

    asyncio.run(scenario())


def test_scheduler_starts_and_stops(worker_env: None) -> None:
    from app.workers import scheduler

    async def scenario() -> None:
        stop = asyncio.Event()
        await asyncio.wait_for(asyncio.gather(scheduler.main(stop), _stop_after(stop, 1.5)), timeout=30)

    asyncio.run(scenario())
