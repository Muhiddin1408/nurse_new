"""FastAPI ilova: `uvicorn app.main:app --host 0.0.0.0 --port 8080`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from medbron_common.app import create_app
from medbron_common.health import database_check, redis_check

from app.clients import elastic
from app.core.config import get_settings
from app.core.database import get_engine
from app.core.redis import get_redis
from app.routers import admin, internal, public


def build_app() -> FastAPI:
    settings = get_settings()
    engine = get_engine() if settings.effective_database_url else None
    redis = get_redis() if settings.redis_url else None
    es = elastic.get_es()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.redis = redis
        yield
        await es.close()
        if redis is not None:
            await redis.aclose()
        if engine is not None:
            await engine.dispose()

    async def es_check() -> None:
        await elastic.ping(es)

    checks: dict[str, Any] = {"elasticsearch": es_check}
    if engine is not None:
        checks["db"] = database_check(engine)
    if redis is not None:
        checks["redis"] = redis_check(redis)

    return create_app(
        settings,
        routers=[public.router, admin.router, internal.router],
        readiness_checks=checks,
        lifespan=lifespan,
        engine=engine,
    )


app = build_app()
