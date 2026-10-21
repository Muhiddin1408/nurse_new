from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

# app import qilinishidan OLDIN — test muhiti (Settings lru_cache).
os.environ.setdefault("SERVICE_NAME", "file-service")
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
S3_ACCESS, S3_SECRET = "file-service", "file-service-secret-123"
INFRA_S3 = Path(__file__).resolve().parents[2] / "infra" / "s3" / "entrypoint.sh"

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
    from app.core.storage import get_storage
    from app.events import get_consumer

    for fn in (get_consumer, get_session_factory, get_engine, get_storage, get_settings):
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
def s3_url() -> Iterator[str]:
    """Haqiqiy S3 ombori — infra'dagi bilan bir xil image va entrypoint (bucketlar, huquqlar)."""
    container = (
        DockerContainer("chrislusf/seaweedfs:4.48")
        .with_env("S3_ADMIN_ACCESS_KEY", "admin")
        .with_env("S3_ADMIN_SECRET_KEY", "admin-secret-123")
        .with_env("S3_FILE_SERVICE_ACCESS_KEY", S3_ACCESS)
        .with_env("S3_FILE_SERVICE_SECRET_KEY", S3_SECRET)
        .with_volume_mapping(str(INFRA_S3), "/medbron-s3.sh", "ro")
        .with_kwargs(entrypoint=["/medbron-s3.sh"])
        .with_exposed_ports(8333)
    )
    with container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8333)}"
        for _ in range(120):
            code, _out = container.exec(["test", "-f", "/tmp/ready"])  # noqa: S108 — konteyner ichidagi fayl
            if code == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("S3 konteyneri tayyor bo'lmadi")
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
    database.scalar("TRUNCATE files, outbox_events, processed_events CASCADE")
    return database


@pytest.fixture
def api(db: Db, s3_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Haqiqiy Postgres + S3 bilan ilova (presigned URL ham shu manzilga — test hostdan yuklaydi)."""
    monkeypatch.setenv("DATABASE_URL", db.url.replace("+asyncpg", ""))
    monkeypatch.setenv("S3_ENDPOINT_URL", s3_url)
    monkeypatch.setenv("S3_PUBLIC_ENDPOINT_URL", s3_url)
    monkeypatch.setenv("S3_ACCESS_KEY", S3_ACCESS)
    monkeypatch.setenv("S3_SECRET_KEY", S3_SECRET)
    reset_caches()
    from app.main import build_app

    app = build_app()
    app.state.security = security_for_tests()
    app.state.test_db_url = db.url
    with TestClient(app) as test_client:
        yield test_client
    reset_caches()
