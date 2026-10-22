from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator

# app import qilinishidan OLDIN — test muhiti (Settings lru_cache).
os.environ.setdefault("SERVICE_NAME", "__SERVICE_NAME__")
os.environ.setdefault("ENVIRONMENT", "test")
# Dev `.env` (scripts/dev-env.py) testga sizib kirmasin — testlar faqat o'z muhitida
os.environ["MEDBRON_ENV_FILE"] = ""

import aio_pika
import asyncpg
import pytest
from fastapi.testclient import TestClient
from testcontainers.core.container import DockerContainer

from medbron_common.security import SecurityConfig
from medbron_common.testing import KeyPair, generate_keypair

USER_KEYS = generate_keypair()
ADMIN_KEYS = generate_keypair()


@pytest.fixture(scope="session")
def user_keys() -> KeyPair:
    return USER_KEYS


@pytest.fixture(scope="session")
def admin_keys() -> KeyPair:
    return ADMIN_KEYS


@pytest.fixture
def client() -> TestClient:
    from app.main import app
    from app.routers.internal import ALLOWED_CALLERS

    app.state.security = SecurityConfig.for_tests(
        user_public_pem=USER_KEYS.public_pem,
        admin_public_pem=ADMIN_KEYS.public_pem,
        inbound_tokens={caller: "test-token" for caller in (*ALLOWED_CALLERS, "test-caller")},
    )
    return TestClient(app)


def _wait(check, attempts: int = 120) -> None:
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
