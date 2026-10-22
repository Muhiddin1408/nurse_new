from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from typing import Any, TypeVar

# app import qilinishidan OLDIN — test muhiti (Settings lru_cache).
os.environ.setdefault("SERVICE_NAME", "analytics-service")
os.environ.setdefault("ENVIRONMENT", "test")
# Dev `.env` (scripts/dev-env.py) testga sizib kirmasin — testlar faqat o'z muhitida
os.environ["MEDBRON_ENV_FILE"] = ""

import aio_pika
import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer

from medbron_common.eventbus import Envelope, build_envelope
from medbron_common.events import CATALOG
from medbron_common.security import SecurityConfig
from medbron_common.testing import KeyPair, generate_keypair

USER_KEYS = generate_keypair()
ADMIN_KEYS = generate_keypair()
CLICKHOUSE_IMAGE = "clickhouse/clickhouse-server:25.8.33.6-alpine"  # docker-compose.yml bilan bir xil
CH_USER, CH_PASSWORD, CH_DB = "analytics", "test-password", "analytics"

T = TypeVar("T")


@pytest.fixture(scope="session")
def user_keys() -> KeyPair:
    return USER_KEYS


@pytest.fixture(scope="session")
def admin_keys() -> KeyPair:
    return ADMIN_KEYS


def reset_caches() -> None:
    from app.core.config import get_settings
    from app.core.database import get_engine, get_session_factory

    for fn in (get_session_factory, get_engine, get_settings):
        fn.cache_clear()


def security_for_tests() -> SecurityConfig:
    return SecurityConfig.for_tests(
        user_public_pem=USER_KEYS.public_pem,
        admin_public_pem=ADMIN_KEYS.public_pem,
        inbound_tokens={"test-caller": "test-token"},
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Bazasiz ilova (live, auth zanjiri) — Docker talab qilmaydi. ClickHouse ulanmagan: ready 503."""
    reset_caches()
    from app.main import build_app

    app = build_app()
    app.state.security = security_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    reset_caches()


def _wait(check: Callable[[], Awaitable[None]], attempts: int = 120) -> None:
    for attempt in range(attempts):
        try:
            asyncio.run(check())
            return
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    container = (
        DockerContainer("postgres:16-alpine")
        .with_env("POSTGRES_USER", "test")
        .with_env("POSTGRES_PASSWORD", "test")
        .with_env("POSTGRES_DB", "test")
        .with_exposed_ports(5432)
    )
    with container:
        url = f"postgresql://test:test@{container.get_container_host_ip()}:{container.get_exposed_port(5432)}/test"

        async def ping() -> None:
            await (await asyncpg.connect(url)).close()

        _wait(ping)
        yield url


@pytest.fixture(scope="session")
def rabbitmq_url() -> Iterator[str]:
    container = (
        DockerContainer("rabbitmq:3.13-alpine")
        .with_env("RABBITMQ_DEFAULT_USER", "test")
        .with_env("RABBITMQ_DEFAULT_PASS", "test")
        .with_exposed_ports(5672)
    )
    with container:
        url = f"amqp://test:test@{container.get_container_host_ip()}:{container.get_exposed_port(5672)}/"

        async def ping() -> None:
            await (await aio_pika.connect(url)).close()

        _wait(ping)
        yield url


@pytest.fixture(scope="session")
def clickhouse_url() -> Iterator[str]:
    container = (
        DockerContainer(CLICKHOUSE_IMAGE)
        .with_env("CLICKHOUSE_DB", CH_DB)
        .with_env("CLICKHOUSE_USER", CH_USER)
        .with_env("CLICKHOUSE_PASSWORD", CH_PASSWORD)
        .with_env("CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT", "0")
        .with_exposed_ports(8123)
    )
    with container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8123)}"

        async def ping() -> None:
            async with httpx.AsyncClient() as http:
                r = await http.get(
                    f"{url}/?query=SELECT%201", headers={"X-ClickHouse-User": CH_USER, "X-ClickHouse-Key": CH_PASSWORD}
                )
                assert r.status_code == 200

        _wait(ping)
        os.environ["CLICKHOUSE_URL"] = url
        os.environ["CLICKHOUSE_USER"] = CH_USER
        os.environ["CLICKHOUSE_PASSWORD"] = CH_PASSWORD
        reset_caches()
        Ch(url).run(lambda ch: _migrate(ch))
        yield url


async def _migrate(ch: Any) -> list[int]:
    from app.clickhouse.schema import migrate

    return await migrate(ch)


class Ch:
    """Testdan ClickHouse: har chaqiruv o'z event loop'i va client'i bilan (TestClient boshqa loop'da)."""

    def __init__(self, url: str) -> None:
        self.url = url

    def run(self, fn: Callable[[Any], Awaitable[T]]) -> T:
        from app.clients.clickhouse import ClickHouse

        async def runner() -> T:
            ch = ClickHouse(self.url, user=CH_USER, password=CH_PASSWORD, database=CH_DB)
            try:
                return await fn(ch)
            finally:
                await ch.aclose()

        return asyncio.run(runner())

    def insert(self, envelopes: list[dict[str, Any]]) -> None:
        from app.services.ingest import to_row

        rows = [to_row(Envelope.model_validate(e), datetime.now(UTC)) for e in envelopes]
        self.run(lambda ch: ch.insert("events_raw", rows))

    def scalar(self, sql: str) -> Any:
        rows = self.run(lambda ch: ch.select(sql))
        return next(iter(rows[0].values())) if rows else None


@pytest.fixture
def ch(clickhouse_url: str) -> Ch:
    db = Ch(clickhouse_url)
    db.run(lambda c: c.command("TRUNCATE TABLE events_raw"))
    return db


def envelope(event_type: str, data: dict[str, Any], *, occurred_at: datetime, **kw: Any) -> dict[str, Any]:
    """Katalog bo'yicha haqiqiy envelope (producer, aggregate — katalogdan)."""
    spec = CATALOG[event_type]
    return build_envelope(
        event_type=event_type,
        producer=spec.publishers[0],
        aggregate_type=spec.aggregate,
        aggregate_id=str(data.get("booking_id") or data.get("payment_id") or "x"),
        data=data,
        occurred_at=occurred_at,
        **kw,
    )


@pytest.fixture
def api(ch: Ch, postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Haqiqiy ClickHouse + Postgres bilan ilova."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = postgres_url
    command.upgrade(cfg, "head")
    monkeypatch.setenv("DATABASE_URL", postgres_url)
    reset_caches()
    from app.main import build_app

    app = build_app()
    app.state.security = security_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    reset_caches()
