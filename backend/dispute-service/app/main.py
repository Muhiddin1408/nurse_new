"""FastAPI ilova: `uvicorn app.main:app --host 0.0.0.0 --port 8080`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from medbron_common.app import create_app
from medbron_common.health import database_check, redis_check

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.redis import get_redis
from app.routers import admin, internal, public
from app.services.deps import Deps


def build_app() -> FastAPI:
    settings = get_settings()
    engine = get_engine() if settings.effective_database_url else None
    redis = get_redis() if settings.redis_url else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Har upstream — bitta ServiceClient (pool + circuit breaker), lifespan'da yopiladi
        app.state.deps = Deps.create(redis)
        yield
        await app.state.deps.aclose()
        if redis is not None:
            await redis.aclose()
        if engine is not None:
            await engine.dispose()

    checks = {}
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
