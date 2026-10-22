"""FastAPI ilova: `uvicorn app.main:app --host 0.0.0.0 --port 8080`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from medbron_common.app import create_app
from medbron_common.health import HealthCheck, database_check

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.storage import get_storage
from app.routers import admin, internal, public


def storage_check() -> HealthCheck:
    async def check() -> None:
        await get_storage().ping()

    return check


def build_app() -> FastAPI:
    settings = get_settings()
    engine = get_engine() if settings.effective_database_url else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            await engine.dispose()

    checks: dict[str, HealthCheck] = {}
    if engine is not None:
        checks["db"] = database_check(engine)
        checks["s3"] = storage_check()

    return create_app(
        settings,
        routers=[public.router, admin.router, internal.router],
        readiness_checks=checks,
        lifespan=lifespan,
        engine=engine,
    )


app = build_app()
