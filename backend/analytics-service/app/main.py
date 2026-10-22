"""FastAPI ilova: `uvicorn app.main:app --host 0.0.0.0 --port 8080`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from medbron_common.app import create_app
from medbron_common.health import HealthCheck, database_check

from app.clients.clickhouse import ClickHouse
from app.core.config import get_settings
from app.core.database import get_engine
from app.routers import admin, internal


def build_app() -> FastAPI:
    settings = get_settings()
    engine = get_engine() if settings.effective_database_url else None
    clickhouse = ClickHouse.from_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.clickhouse = clickhouse
        yield
        await clickhouse.aclose()
        if engine is not None:
            await engine.dispose()

    checks: dict[str, HealthCheck] = {"clickhouse": clickhouse.ping}
    if engine is not None:
        checks["db"] = database_check(engine)

    return create_app(
        settings,
        routers=[admin.router, internal.router],
        readiness_checks=checks,
        lifespan=lifespan,
        engine=engine,
    )


app = build_app()
