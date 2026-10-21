"""Servislararo HTTP client standarti (BLUEPRINT 7.2).

- bitta `httpx.AsyncClient` har target uchun (connection pooling)
- timeout majburiy: connect 2s, read 5s
- retry FAQAT idempotent so'rovlarda (GET/HEAD yoki `Idempotency-Key` li so'rov),
  exponential backoff + jitter, max 3 urinish
- circuit breaker (5 ketma-ket xato → 30s ochiq)
- `X-Internal-Token`, `X-Caller`, `X-Request-ID`, `traceparent` avtomatik
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable
from typing import Any

import httpx

from medbron_common.circuit import CircuitBreaker
from medbron_common.context import get_request_id
from medbron_common.errors import AppError
from medbron_common.metrics import UPSTREAM_REQUESTS
from medbron_common.telemetry import inject_trace_headers

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(5.0, connect=2.0)
RETRYABLE_STATUSES = frozenset({502, 503, 504})
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class UpstreamUnavailable(AppError):
    """Target servis javob bermadi / circuit ochiq. Chaqiruvchiga 503 sifatida chiqadi."""

    status_code = 503
    code = "UPSTREAM_UNAVAILABLE"
    message = "Bog'liq servis vaqtincha mavjud emas"


class UpstreamError(AppError):
    """Target servis xato javob qaytardi (4xx/5xx). `upstream_status`, `upstream_code` saqlanadi.

    Standart holatda chaqiruvchiga 502 bo'lib chiqadi — ichki xato kodlari tashqariga
    tasodifan sizib chiqmasligi uchun. Kerakli kodlarni chaqiruvchi o'zi tarjima qiladi.
    """

    status_code = 502
    code = "UPSTREAM_ERROR"
    message = "Bog'liq servis xato qaytardi"

    def __init__(self, target: str, response: httpx.Response) -> None:
        self.target = target
        self.upstream_status = response.status_code
        self.upstream_code: str | None = None
        self.upstream_message: str | None = None
        # To'liq `detail` (masalan, PROMO_INVALID ning `reason` i) — chaqiruvchi tarjima qilishi uchun
        self.upstream_detail: dict[str, Any] = {}
        try:
            detail = response.json().get("detail")
            if isinstance(detail, dict):
                self.upstream_code = detail.get("code")
                self.upstream_message = detail.get("message")
                self.upstream_detail = detail
        except (ValueError, AttributeError):
            pass
        super().__init__(extra={"target": target, "upstream_status": self.upstream_status})


class ServiceClient:
    def __init__(
        self,
        target: str,
        base_url: str,
        *,
        caller: str,
        token: str | None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_attempts: int = 3,
        backoff_base: float = 0.1,
        backoff_max: float = 2.0,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_connections: int = 100,
    ) -> None:
        self.target = target
        self.caller = caller
        self._token = token
        self.max_attempts = max(1, max_attempts)
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.breaker = breaker or CircuitBreaker(target)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=20),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ServiceClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _headers(self, extra: dict[str, str] | None, idempotency_key: str | None) -> dict[str, str]:
        headers: dict[str, str] = {"X-Caller": self.caller}
        if self._token:
            headers["X-Internal-Token"] = self._token
        request_id = get_request_id()
        if request_id:
            headers["X-Request-ID"] = request_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        inject_trace_headers(headers)
        if extra:
            headers.update(extra)
        return headers

    def _delay(self, attempt: int) -> float:
        # Full jitter: bir vaqtda yiqilgan klientlar bir vaqtda qaytib kelmasin.
        cap = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))
        return random.uniform(0, cap)  # noqa: S311 — kriptografik emas

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        method = method.upper()
        retryable = method in IDEMPOTENT_METHODS or idempotency_key is not None
        attempts = self.max_attempts if retryable else 1
        req_headers = self._headers(headers, idempotency_key)
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            if not self.breaker.allow():
                UPSTREAM_REQUESTS.labels(self.target, method, "circuit_open").inc()
                raise UpstreamUnavailable(extra={"target": self.target, "reason": "circuit_open"})
            try:
                response = await self._client.request(method, path, json=json, params=params, headers=req_headers)
            except httpx.TransportError as exc:
                self.breaker.record_failure()
                UPSTREAM_REQUESTS.labels(self.target, method, "transport_error").inc()
                last_exc = exc
                logger.warning(
                    "upstream transport error",
                    extra={"target": self.target, "path": path, "attempt": attempt, "error": repr(exc)},
                )
            else:
                if response.status_code in RETRYABLE_STATUSES:
                    self.breaker.record_failure()
                    UPSTREAM_REQUESTS.labels(self.target, method, str(response.status_code)).inc()
                    if attempt < attempts:
                        await asyncio.sleep(self._delay(attempt))
                        continue
                    raise UpstreamUnavailable(extra={"target": self.target, "upstream_status": response.status_code})
                if response.status_code >= 500:
                    self.breaker.record_failure()
                else:
                    # 4xx — servis sog'lom, so'rov noto'g'ri. Zanjirni ochmaydi.
                    self.breaker.record_success()
                UPSTREAM_REQUESTS.labels(self.target, method, str(response.status_code)).inc()
                if raise_for_status and response.status_code >= 400:
                    raise UpstreamError(self.target, response)
                return response
            if attempt < attempts:
                await asyncio.sleep(self._delay(attempt))

        raise UpstreamUnavailable(extra={"target": self.target, "reason": "transport_error"}) from last_exc

    async def get_json(self, path: str, **kwargs: Any) -> Any:
        return (await self.request("GET", path, **kwargs)).json()

    async def post_json(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        response = await self.request("POST", path, json=json, **kwargs)
        return response.json() if response.content else None


async def with_fallback[T](call: Awaitable[T], default: T) -> T:
    """Ixtiyoriy ma'lumot uchun (BLUEPRINT 7.2): target yiqilsa so'rov yiqilmaydi."""
    try:
        return await call
    except (UpstreamUnavailable, UpstreamError) as exc:
        logger.warning("optional upstream call failed, using fallback", extra={"error": exc.code})
        return default
