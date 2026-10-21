from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

# app import qilinishidan OLDIN — test muhiti (Settings lru_cache).
os.environ.setdefault("SERVICE_NAME", "billing-service")
os.environ.setdefault("ENVIRONMENT", "test")
# Dev `.env` (scripts/dev-env.py) testga sizib kirmasin — testlar faqat o'z muhitida
os.environ["MEDBRON_ENV_FILE"] = ""

import aio_pika
import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.core.container import DockerContainer

from medbron_common.db import normalize_database_url
from medbron_common.security import SecurityConfig
from medbron_common.testing import KeyPair, generate_keypair

USER_KEYS = generate_keypair()
ADMIN_KEYS = generate_keypair()

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
    from app.core.redis import get_redis
    from app.events import get_consumer

    for fn in (get_consumer, get_session_factory, get_engine, get_redis, get_settings):
        fn.cache_clear()


def security_for_tests() -> SecurityConfig:
    from app.routers.internal import ALLOWED_CALLERS

    return SecurityConfig.for_tests(
        user_public_pem=USER_KEYS.public_pem,
        admin_public_pem=ADMIN_KEYS.public_pem,
        inbound_tokens={caller: "test-token" for caller in (*ALLOWED_CALLERS, "test-caller")},
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Bazasiz ilova (health, auth zanjiri) — Docker talab qilmaydi."""
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
def redis_url() -> Iterator[str]:
    container = DockerContainer("redis:7.4-alpine").with_exposed_ports(6379)
    with container:
        url = f"redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0"

        async def ping() -> None:
            from redis.asyncio import Redis

            client = Redis.from_url(url)
            try:
                await client.ping()
            finally:
                await client.aclose()

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
def migrated_db(postgres_url: str) -> str:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = postgres_url
    command.upgrade(cfg, "head")
    return postgres_url


class Db:
    """Testdan to'g'ridan-to'g'ri baza: har chaqiruv o'z event loop'i va NullPool engine'i bilan
    (TestClient ilovasi boshqa loop'da ishlaydi — ulanishlar aralashmasin)."""

    def __init__(self, url: str) -> None:
        self.url = normalize_database_url(url)

    def run(self, fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
        async def runner() -> T:
            engine = create_async_engine(self.url, poolclass=NullPool)
            try:
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as session:
                    result = await fn(session)
                    await session.commit()
                    return result
            finally:
                await engine.dispose()

        return asyncio.run(runner())

    def scalar(self, sql: str, **params: Any) -> Any:
        from sqlalchemy import text

        async def fn(session: AsyncSession) -> Any:
            result = await session.execute(text(sql), params)
            return result.scalar() if result.returns_rows else None

        return self.run(fn)

    def all(self, sql: str, **params: Any) -> list[Any]:
        from sqlalchemy import text

        async def fn(session: AsyncSession) -> list[Any]:
            return list((await session.execute(text(sql), params)).all())

        return self.run(fn)


@pytest.fixture
def db(migrated_db: str) -> Db:
    database = Db(migrated_db)
    database.scalar("TRUNCATE earning_entries, payouts, payout_accounts, outbox_events, processed_events CASCADE")
    return database


@pytest.fixture
def api(db: Db, redis_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Haqiqiy Postgres + Redis bilan ilova."""
    monkeypatch.setenv("DATABASE_URL", db.url.replace("+asyncpg", ""))
    monkeypatch.setenv("REDIS_URL", redis_url)

    async def flush() -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url)
        try:
            await client.flushall()
        finally:
            await client.aclose()

    asyncio.run(flush())
    reset_caches()
    from app.main import build_app

    app = build_app()
    app.state.security = security_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    reset_caches()


@pytest.fixture
def run_handler(db: Db) -> Callable[..., None]:
    """Katalogdagi event'ni servis handleri orqali (consumer tranzaksiyasi kabi) qo'llaydi."""
    from datetime import UTC, datetime

    from medbron_common.eventbus import Envelope, EventConsumer, build_envelope
    from medbron_common.events import CATALOG

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="billing-service",
        amqp_url="amqp://unused/",
        session_factory=None,  # type: ignore[arg-type]
        processed_model=ProcessedEvent,
    )
    from tests.fakes import FakeDeps

    deps = FakeDeps()
    handlers.register(consumer, deps)  # type: ignore[arg-type]

    def run(event_type: str, data: dict[str, Any], *, occurred_at: datetime | None = None) -> None:
        """Handler'larning upstream'lari: `run.deps` (FakeDeps) — test o'zgartirishi mumkin."""
        spec = CATALOG[event_type]
        raw = build_envelope(
            event_type=event_type,
            producer=spec.publishers[0],
            aggregate_type=spec.aggregate,
            aggregate_id=str(data.get("booking_id") or data.get("refund_id") or data.get("payment_id") or "x"),
            data=data,
            occurred_at=occurred_at or datetime.now(UTC),
        )
        from medbron_common.events import validate_event

        validate_event(raw)
        envelope = Envelope.model_validate(raw)
        db.run(lambda session: consumer._handlers[event_type].handler(session, envelope))

    run.deps = deps  # type: ignore[attr-defined]
    return run
