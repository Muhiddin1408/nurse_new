"""Ingest worker kirish nuqtasi haqiqiy RabbitMQ + ClickHouse bilan: ishga tushadi va SIGTERM (stop) da toza
to'xtaydi; CLI ClickHouse migratsiyasi.

Docker talab qiladi (testcontainers): `pytest -m integration`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from tests.conftest import reset_caches

pytestmark = pytest.mark.integration


@pytest.fixture
def worker_env(rabbitmq_url: str, clickhouse_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RABBITMQ_URL", rabbitmq_url)
    monkeypatch.setenv("WORKER_METRICS_PORT", "0")
    monkeypatch.setenv("INGEST_FLUSH_SECONDS", "0.2")
    reset_caches()
    yield
    reset_caches()


async def _stop_after(stop: asyncio.Event, seconds: float) -> None:
    await asyncio.sleep(seconds)
    stop.set()


def test_event_consumer_starts_and_stops(worker_env: None) -> None:
    from app.workers import event_consumer

    async def scenario() -> None:
        stop = asyncio.Event()
        await asyncio.wait_for(asyncio.gather(event_consumer.main(stop), _stop_after(stop, 1.5)), timeout=30)

    asyncio.run(scenario())


def test_event_consumer_requires_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers import event_consumer

    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    reset_caches()
    with pytest.raises(RuntimeError, match="RABBITMQ_URL"):
        asyncio.run(event_consumer.main(asyncio.Event()))
    reset_caches()


def test_cli_clickhouse_migrate(worker_env: None, capsys: pytest.CaptureFixture[str]) -> None:
    from app import cli

    assert cli.main(["clickhouse-migrate"]) == 0
    assert "qo'llandi" in capsys.readouterr().out
