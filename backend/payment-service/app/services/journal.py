"""Provayder bilan almashinuv jurnali (`provider_logs`) — biznes tranzaksiyasidan ALOHIDA sessiyada: to'lov
tranzaksiyasi rollback bo'lsa ham dalil saqlanadi. Jurnal yozilmasa — so'rov yiqilmaydi (faqat log)."""

from __future__ import annotations

import logging
from typing import Any

from app.clients.providers import Exchange
from app.core.database import get_session_factory
from app.repositories import payments as repo

logger = logging.getLogger(__name__)

# Logga hech qachon tushmaydigan maydonlar (imzo, kalit)
SECRET_FIELDS = frozenset({"sign_string", "password", "Auth"})


def scrub(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in SECRET_FIELDS}


async def write(
    *,
    provider: str,
    direction: str,
    operation: str,
    request: dict[str, Any],
    response: dict[str, Any],
    error: str = "",
    ip: str | None = None,
    duration_ms: int = 0,
) -> None:
    try:
        async with get_session_factory()() as session, session.begin():
            repo.add_log(
                session,
                provider=provider,
                direction=direction,
                operation=operation,
                request=scrub(request),
                response=response,
                error=error,
                ip=ip,
                duration_ms=duration_ms,
            )
    except Exception:  # pragma: no cover — jurnal xatosi pul oqimini to'xtatmaydi
        logger.exception("provider log write failed", extra={"provider": provider, "operation": operation})


async def outbound(exchange: Exchange) -> None:
    await write(
        provider=exchange.provider,
        direction="out",
        operation=exchange.operation,
        request=exchange.request,
        response=exchange.response,
        error=exchange.error,
        duration_ms=exchange.duration_ms,
    )
