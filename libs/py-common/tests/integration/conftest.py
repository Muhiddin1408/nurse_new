"""Integratsiya testlari: haqiqiy Postgres 16 va RabbitMQ 3.13 (testcontainers).

Har test o'z topologiyasini (noyob prefiks, qisqa retry TTL) oladi — testlar bir-birining
DLQ/retry navbatlariga aralashmaydi.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import aio_pika
import asyncpg
import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from testcontainers.core.container import DockerContainer

from medbron_common.db import create_engine, create_session_factory, make_metadata
from medbron_common.eventbus import OutboxMixin, ProcessedEventMixin, Topology

pytestmark = pytest.mark.integration

PG_IMAGE = "postgres:16-alpine"
RABBIT_IMAGE = "rabbitmq:3.13-alpine"


class Base(DeclarativeBase):
    metadata = make_metadata()


class OutboxEvent(OutboxMixin, Base):
    pass


class ProcessedEvent(ProcessedEventMixin, Base):
    pass


class Note(Base):
    """Biznes jadvali o'rnida — handler effekti atomikligini tekshirish uchun."""

    __tablename__ = "notes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)


async def _wait_for(check, timeout: float = 60.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            await check()
            return
        except Exception:
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(0.5)


@pytest.fixture(scope="session")
def postgres() -> Iterator[str]:
    container = (
        DockerContainer(PG_IMAGE)
        .with_env("POSTGRES_USER", "test")
        .with_env("POSTGRES_PASSWORD", "test")
        .with_env("POSTGRES_DB", "test")
        .with_exposed_ports(5432)
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        yield f"postgresql://test:test@{host}:{port}/test"


@pytest.fixture(scope="session")
def rabbitmq() -> Iterator[str]:
    container = (
        DockerContainer(RABBIT_IMAGE)
        .with_env("RABBITMQ_DEFAULT_USER", "test")
        .with_env("RABBITMQ_DEFAULT_PASS", "test")
        .with_exposed_ports(5672)
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5672)
        yield f"amqp://test:test@{host}:{port}/"


@pytest.fixture(scope="session")
async def engine(postgres: str) -> AsyncIterator[AsyncEngine]:
    async def ping() -> None:
        conn = await asyncpg.connect(postgres)
        await conn.close()

    await _wait_for(ping)
    eng = create_engine(postgres)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
async def amqp_url(rabbitmq: str) -> str:
    async def ping() -> None:
        conn = await aio_pika.connect(rabbitmq)
        await conn.close()

    await _wait_for(ping, timeout=90)
    return rabbitmq


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield create_session_factory(engine)


@pytest.fixture
def topology() -> Topology:
    prefix = f"t{uuid.uuid4().hex[:8]}"
    return Topology(
        exchange=f"{prefix}.events",
        dlx=f"{prefix}.dlx",
        dlq=f"{prefix}.dlq",
        retry_exchange=f"{prefix}.retry",
        retry_tiers=(("a", 200), ("b", 300), ("c", 400)),
    )


async def drain_dlq(amqp_url: str, topology: Topology, *, expect: int, timeout: float = 10.0) -> list[Any]:
    """DLQ dan `expect` ta xabar kelguncha kutadi va ularni qaytaradi."""
    conn = await aio_pika.connect(amqp_url)
    messages: list[Any] = []
    try:
        channel = await conn.channel()
        queue = await channel.get_queue(topology.dlq)
        deadline = asyncio.get_running_loop().time() + timeout
        while len(messages) < expect and asyncio.get_running_loop().time() < deadline:
            msg = await queue.get(no_ack=True, fail=False)
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            messages.append(msg)
        # Kutilganidan ortiq kelmaganini tekshirish uchun qisqa kutish
        await asyncio.sleep(0.3)
        while (extra := await queue.get(no_ack=True, fail=False)) is not None:
            messages.append(extra)
    finally:
        await conn.close()
    return messages


def body_of(message: Any) -> dict[str, Any]:
    return json.loads(message.body)
