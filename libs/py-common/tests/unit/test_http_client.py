from __future__ import annotations

import httpx
import pytest

from medbron_common.circuit import CircuitBreaker
from medbron_common.context import request_id_var
from medbron_common.http_client import ServiceClient, UpstreamError, UpstreamUnavailable, with_fallback


class Recorder:
    def __init__(self, responses: list[int | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if self.responses else 200
        if isinstance(item, Exception):
            raise item
        if item >= 400:
            return httpx.Response(item, json={"detail": {"code": "SOMETHING", "message": "m"}})
        return httpx.Response(item, json={"ok": True})


def make(rec: Recorder, **kw) -> ServiceClient:
    kw.setdefault("backoff_base", 0.0)
    return ServiceClient(
        "catalog-service",
        "http://catalog-service:8080",
        caller="booking-service",
        token="tok",
        transport=httpx.MockTransport(rec),
        **kw,
    )


async def test_headers_injected() -> None:
    rec = Recorder([200])
    token = request_id_var.set("req-1")
    try:
        async with make(rec) as c:
            assert await c.get_json("/internal/v1/x") == {"ok": True}
    finally:
        request_id_var.reset(token)
    h = rec.requests[0].headers
    assert h["X-Internal-Token"] == "tok"
    assert h["X-Caller"] == "booking-service"
    assert h["X-Request-ID"] == "req-1"


async def test_get_retries_on_503_then_succeeds() -> None:
    rec = Recorder([503, httpx.ConnectError("x"), 200])
    async with make(rec) as c:
        await c.get_json("/x")
    assert len(rec.requests) == 3


async def test_post_without_idempotency_key_not_retried() -> None:
    rec = Recorder([503, 200])
    async with make(rec) as c:
        with pytest.raises(UpstreamUnavailable):
            await c.post_json("/x", {"a": 1})
    assert len(rec.requests) == 1


async def test_post_with_idempotency_key_retried() -> None:
    rec = Recorder([httpx.ReadTimeout("t"), 200])
    async with make(rec) as c:
        await c.post_json("/x", {"a": 1}, idempotency_key="k1")
    assert len(rec.requests) == 2
    assert rec.requests[1].headers["Idempotency-Key"] == "k1"


async def test_4xx_raises_upstream_error_without_retry() -> None:
    rec = Recorder([404])
    async with make(rec) as c:
        with pytest.raises(UpstreamError) as exc:
            await c.get_json("/x")
    assert exc.value.upstream_status == 404
    assert exc.value.upstream_code == "SOMETHING"
    assert len(rec.requests) == 1
    assert exc.value.status_code == 502  # ichki kod tashqariga sizib chiqmaydi


async def test_circuit_opens_and_short_circuits() -> None:
    rec = Recorder([500] * 10)
    breaker = CircuitBreaker("catalog-service", failure_threshold=2, recovery_timeout=60)
    async with make(rec, breaker=breaker, max_attempts=1) as c:
        for _ in range(2):
            with pytest.raises(UpstreamError):
                await c.get_json("/x")
        with pytest.raises(UpstreamUnavailable) as exc:
            await c.get_json("/x")
    assert exc.value.extra["reason"] == "circuit_open"
    assert len(rec.requests) == 2


async def test_with_fallback() -> None:
    rec = Recorder([httpx.ConnectError("down")] * 3)
    async with make(rec) as c:
        assert await with_fallback(c.get_json("/avatar"), None) is None
