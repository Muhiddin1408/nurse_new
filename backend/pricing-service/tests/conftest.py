from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

# app import qilinishidan OLDIN — test muhiti (Settings lru_cache).
os.environ.setdefault("SERVICE_NAME", "pricing-service")
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
    database.scalar(
        "TRUNCATE services_cache, client_bookings, promo_redemptions, promo_codes, package_uses, "
        "package_enrollments, packages, outbox_events, processed_events CASCADE"
    )
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
        service_name="pricing-service",
        amqp_url="amqp://unused/",
        session_factory=None,  # type: ignore[arg-type]
        processed_model=ProcessedEvent,
    )
    handlers.register(consumer)

    def run(event_type: str, data: dict[str, Any], *, occurred_at: datetime | None = None) -> dict[str, Any]:
        spec = CATALOG[event_type]
        raw = build_envelope(
            event_type=event_type,
            producer=spec.publishers[0],
            aggregate_type=spec.aggregate,
            aggregate_id=str(data.get("booking_id") or data.get("user_id") or "x"),
            data=data,
            occurred_at=occurred_at or datetime.now(UTC),
        )
        from medbron_common.events import validate_event

        validate_event(raw)
        envelope = Envelope.model_validate(raw)
        db.run(lambda session: consumer._handlers[event_type].handler(session, envelope))
        return raw

    return run


# --- soxta catalog va admin (ularning o'z testlari bor; bu yerda faqat HTTP kontrakt) ------------

PLATFORM_SETTINGS: dict[str, Any] = {
    "commission": {"platform_bp": 1500, "payme_fee_bp": 100, "click_fee_bp": 100},
    "payment": {"deposit_percent": 20, "force_prepaid_after_no_shows": 2},
    "pricing": {"first_booking_discount_percent": 0},
    "fiscal": {"default_mxik_code": "06201001001000000", "default_package_code": "1501436", "default_vat_percent": 0,
               "tin": ""},
}  # fmt: skip


class FakeCatalog:
    def __init__(self) -> None:
        import uuid as _uuid

        self._uuid = _uuid
        self.doctors: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self.service_calls = 0
        self.down = False

    def add_doctor(self, *, mode: str = "prepaid", follow_up: int = 0, bookable: bool = True) -> Any:
        doctor_id = self._uuid.uuid4()
        self.doctors[str(doctor_id)] = {
            "doctor_id": str(doctor_id), "user_id": str(self._uuid.uuid4()), "status": "approved",
            "bookable": bookable, "full_name": "Dr", "accepts_home_visits": True, "home_visit_radius_km": 10,
            "home_base": None, "clinic_payment_mode": mode, "follow_up_discount_percent": follow_up,
            "specialization_ids": [], "clinic_ids": [],
        }  # fmt: skip
        return doctor_id

    def add_service(
        self,
        doctor_id: Any,
        *,
        price_tiyin: int = 15_000_000,
        place: str = "clinic",
        mxik: str = "",
        active: bool = True,
    ) -> Any:
        service_id = self._uuid.uuid4()
        self.services[str(service_id)] = {
            "id": str(service_id), "doctor_id": str(doctor_id) if doctor_id else None, "clinic_id": None,
            "specialization_id": str(self._uuid.uuid4()), "name": f"Xizmat {len(self.services) + 1}", "name_ru": "",
            "place": place, "duration_minutes": 30, "price_tiyin": price_tiyin, "is_active": active,
            "mxik_code": mxik, "package_code": "", "vat_percent": None,
            "patient_rules": {"is_pediatric": False, "accepts_children": True, "min_patient_age": None,
                              "max_patient_age": None, "allowed_gender": ""},
        }  # fmt: skip
        return service_id

    def __call__(self, request: Any) -> Any:
        import httpx

        if self.down:
            return httpx.Response(503)
        path = request.url.path
        if path.endswith("/bookability"):
            doctor = self.doctors.get(path.split("/")[-2])
            if doctor is None:
                return httpx.Response(404, json={"detail": {"code": "DOCTOR_NOT_FOUND", "message": "x"}})
            return httpx.Response(200, json=doctor)
        if path == "/internal/v1/services":
            self.service_calls += 1
            ids = request.url.params["ids"].split(",")
            return httpx.Response(200, json={"items": [self.services[i] for i in ids if i in self.services]})
        return httpx.Response(404)


class World:
    def __init__(self, app: Any, catalog: FakeCatalog) -> None:
        self.app = app
        self.catalog = catalog
        self.settings = PLATFORM_SETTINGS


@pytest.fixture
def world(api: TestClient) -> World:
    """`api` ilovasining catalog/admin clientlarini MockTransport bilan almashtiradi (Redis — ilovaniki)."""
    import copy

    import httpx

    from medbron_common.http_client import ServiceClient
    from medbron_common.settings_client import PlatformSettingsClient

    from app.services.quotes import Deps

    catalog = FakeCatalog()
    settings = copy.deepcopy(PLATFORM_SETTINGS)
    state = {"version": 1}

    def admin_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": state["version"], "values": settings})

    deps: Deps = api.app.state.deps

    def client(target: str, handler: Any) -> ServiceClient:
        return ServiceClient(target, f"http://{target}", caller="pricing-service", token="t",
                             transport=httpx.MockTransport(handler), backoff_base=0, max_attempts=1)  # fmt: skip

    deps.catalog.http = client("catalog-service", catalog)
    deps.admin.http = client("admin-service", admin_handler)
    deps.admin.settings = PlatformSettingsClient(deps.admin.http, deps.redis)
    world = World(api.app, catalog)
    world.settings = settings
    return world
