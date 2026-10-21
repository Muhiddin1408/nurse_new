"""`/health/live` va `/health/ready` (BLUEPRINT 5.3).

live  — jarayon tirik; tashqi bog'liqliklar TEKSHIRILMAYDI (aks holda DB nosozligida
        orkestrator hamma podlarni o'ldirib, muammoni kuchaytiradi).
ready — DB, Redis, broker; biri yiqilsa 503 va trafik bu nusxaga yuborilmaydi.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

HealthCheck = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0


async def _run(name: str, check: HealthCheck) -> tuple[str, str | None]:
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        return name, None
    except TimeoutError:
        return name, "timeout"
    except Exception as exc:
        return name, f"{type(exc).__name__}: {exc}"[:200]


def build_health_router(checks: Mapping[str, HealthCheck] | None = None) -> APIRouter:
    router = APIRouter(tags=["health"])
    registered = dict(checks or {})

    @router.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        results = await asyncio.gather(*(_run(n, c) for n, c in registered.items()))
        failed = {name: err for name, err in results if err is not None}
        body: dict[str, Any] = {
            "status": "ok" if not failed else "unavailable",
            "checks": {name: ("ok" if err is None else err) for name, err in results},
        }
        if failed:
            logger.warning("readiness failed", extra={"failed": failed})
        return JSONResponse(body, status_code=200 if not failed else 503)

    return router


def database_check(engine: AsyncEngine) -> HealthCheck:
    async def check() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return check


def redis_check(client: Any) -> HealthCheck:
    async def check() -> None:
        if not await client.ping():
            raise RuntimeError("PING javob bermadi")

    return check


def broker_check(get_connection: Callable[[], Any]) -> HealthCheck:
    """`get_connection` — joriy aio-pika ulanishini qaytaruvchi funksiya (None bo'lishi mumkin)."""

    async def check() -> None:
        conn = get_connection()
        if conn is None or conn.is_closed:
            raise RuntimeError("broker ulanishi yo'q")

    return check
