"""FastAPI ilova: `uvicorn app.main:app --host 0.0.0.0 --port 8080`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from medbron_common.app import create_app
from medbron_common.health import database_check, redis_check
from medbron_common.sms import RedisTokenStore, create_sms_client

from app.core.config import get_settings
from app.core.database import get_engine
from app.core.redis import get_redis
from app.core.tokens import get_signer
from app.routers import admin, internal, public
from app.services.ratelimit import OtpLimits, OtpRateLimiter


def build_app() -> FastAPI:
    settings = get_settings()
    engine = get_engine() if settings.effective_database_url else None
    redis = get_redis() if settings.redis_url else None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.environment not in ("local", "test"):
            get_signer()  # imzo kaliti yo'q bo'lsa — darhol yiqiladi (fail-fast), "yarim ishlaydigan" servis emas
        if redis is not None:
            app.state.otp_limiter = OtpRateLimiter(
                redis,
                OtpLimits(
                    cooldown_seconds=settings.otp_resend_cooldown_seconds,
                    per_phone_hour=settings.otp_max_per_phone_hour,
                    per_ip_hour=settings.otp_max_per_ip_hour,
                    failed_verify_per_phone_hour=settings.otp_max_failed_verify_per_phone_hour,
                ),
            )
        app.state.sms = create_sms_client(
            settings.sms_config(),
            environment=settings.environment,
            token_store=RedisTokenStore(redis) if redis is not None else None,
        )
        yield
        await app.state.sms.aclose()
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
