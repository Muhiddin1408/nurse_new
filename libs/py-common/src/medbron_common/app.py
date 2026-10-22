"""FastAPI ilova fabrikasi — har servis bir xil poydevor oladi.

app = create_app(
    settings,
    routers=[bookings.router, admin_bookings.router, internal_bookings.router],
    readiness_checks={"db": database_check(engine), "redis": redis_check(redis)},
    lifespan=lifespan,
)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI

from medbron_common import __version__
from medbron_common.config import ServiceSettings
from medbron_common.errors import install_error_handlers
from medbron_common.health import HealthCheck, build_health_router
from medbron_common.logging import configure_logging
from medbron_common.metrics import metrics_endpoint
from medbron_common.middleware import RequestContextMiddleware
from medbron_common.security import SecurityConfig
from medbron_common.telemetry import setup_sentry, setup_tracing

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def create_app(
    settings: ServiceSettings,
    *,
    routers: Sequence[APIRouter] = (),
    readiness_checks: Mapping[str, HealthCheck] | None = None,
    lifespan: Lifespan | None = None,
    security: SecurityConfig | None = None,
    version: str = "0.1.0",
    engine: Any = None,
    configure_logs: bool = True,
) -> FastAPI:
    if configure_logs:
        configure_logging(settings.service_name, settings.log_level)
    setup_sentry(
        settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else None,
        environment=settings.environment,
        release=f"{settings.service_name}@{version}",
    )

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        if lifespan is None:
            yield
        else:
            async with lifespan(app):
                yield

    app = FastAPI(
        title=settings.service_name,
        version=version,
        lifespan=_lifespan,
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )
    app.state.settings = settings
    app.state.security = security or SecurityConfig.from_settings(settings)
    if settings.is_prod:
        # Kalitsiz prod — jim buzilish emas, darhol va shovqinli yiqilish.
        app.state.security.load_eagerly()
    app.state.common_version = __version__

    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(build_health_router(readiness_checks))
    app.add_route("/metrics", metrics_endpoint, include_in_schema=False)
    for router in routers:
        app.include_router(router)

    setup_tracing(
        settings.service_name,
        settings.otel_exporter_otlp_endpoint,
        app=app,
        engine=engine,
        environment=settings.environment,
    )
    return app
