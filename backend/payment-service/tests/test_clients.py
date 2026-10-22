"""Chiquvchi HTTP kontraktlari: booking internal API (contracts/openapi/booking-service.internal.yaml) va
provayder adapterlari (Click reversal / fiskal, retry, circuit breaker, jurnal; ichki token tashqariga chiqmaydi)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from medbron_common.http_client import ServiceClient, UpstreamError

from app.clients.providers import (
    ClickProvider,
    Exchange,
    ManualRefundRequired,
    PaymeProvider,
    ProviderError,
    ProviderGateway,
    ProviderUnavailable,
)
from app.clients.upstream import BookingClient
from tests.fakes import FakeBooking


def wire(client, handler) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def recorder(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client.http = ServiceClient(
        client.target,
        "http://upstream",
        caller="payment-service",
        token="tok",
        transport=httpx.MockTransport(recorder),
        backoff_base=0,
    )
    return seen


async def test_booking_client_payable_and_extend() -> None:
    fake = FakeBooking()
    booking_id = fake.add(uuid.uuid4(), total=5_000_000)
    body = fake.bookings[booking_id]
    client = BookingClient("http://x")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/payable"):
            return (
                httpx.Response(200, json=body)
                if str(booking_id) in request.url.path
                else httpx.Response(404, json={"detail": {"code": "BOOKING_NOT_FOUND", "message": "m"}})
            )
        if request.url.path.endswith("/extend-payment"):
            if str(booking_id) in request.url.path:
                return httpx.Response(200, json={"payment_due_at": "2026-10-21T10:15:00+00:00"})
            return httpx.Response(409, json={"detail": {"code": "NOT_PAYABLE", "message": "m"}})
        return httpx.Response(500)

    seen = wire(client, handler)
    payable = await client.payable(booking_id)
    assert payable is not None and payable.prepay_tiyin == 5_000_000 and payable.money["total_tiyin"] == 5_000_000
    assert payable.clinic_id is not None and payable.items[0]["mxik_code"]
    assert await client.payable(uuid.uuid4()) is None
    assert (await client.extend_payment(booking_id)) is not None
    assert await client.extend_payment(uuid.uuid4()) is None
    assert seen[0].url.path == f"/internal/v1/bookings/{booking_id}/payable"
    assert seen[0].headers["x-caller"] == "payment-service" and seen[0].headers["x-internal-token"] == "tok"
    assert seen[2].method == "POST"

    wire(client, lambda r: httpx.Response(400, json={"detail": {"code": "BAD", "message": "m"}}))
    with pytest.raises(UpstreamError):
        await client.payable(booking_id)
    await client.aclose()


def _gateway(name: str, handler, journal: list[Exchange]) -> ProviderGateway:  # type: ignore[no-untyped-def]
    async def record(exchange: Exchange) -> None:
        journal.append(exchange)

    return ProviderGateway(name, journal=record, transport=httpx.MockTransport(handler))


async def test_click_reversal_success_retry_and_manual() -> None:
    journal: list[Exchange] = []
    calls: list[httpx.Request] = []
    responses = [httpx.Response(502), httpx.Response(200, json={"error_code": 0, "payment_id": 555})]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0)

    provider = ClickProvider(_gateway("click", handler, journal))
    result = await provider.refund(paydoc_id="9001", amount_tiyin=100, payment_amount_tiyin=100)
    assert result.external_refund_id == "555"
    assert [c.method for c in calls] == ["DELETE", "DELETE"]  # 502 → idempotent retry
    assert calls[0].url.path.endswith("/payment/reversal/777/9001")
    auth = calls[0].headers["auth"].split(":")
    assert auth[0] == "4242" and len(auth[1]) == 40
    assert "x-internal-token" not in calls[0].headers  # ichki token provayderga chiqmaydi
    assert [bool(e.error) for e in journal] == [True, False]

    with pytest.raises(ManualRefundRequired):  # qisman — API'da yo'q
        await provider.refund(paydoc_id="9001", amount_tiyin=50, payment_amount_tiyin=100)
    with pytest.raises(ManualRefundRequired):
        await provider.refund(paydoc_id=None, amount_tiyin=100, payment_amount_tiyin=100)
    responses.append(httpx.Response(200, json={"error_code": -5, "error_note": "no"}))
    with pytest.raises(ManualRefundRequired):
        await provider.refund(paydoc_id="9001", amount_tiyin=100, payment_amount_tiyin=100)
    with pytest.raises(ManualRefundRequired):
        await PaymeProvider(_gateway("payme", handler, journal)).refund(
            paydoc_id=None, amount_tiyin=1, payment_amount_tiyin=1
        )
    await provider.gateway.aclose()


async def test_click_fiscal_and_circuit_breaker() -> None:
    journal: list[Exchange] = []
    bodies: list[dict] = []

    def ok(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"error_code": 0})

    provider = ClickProvider(_gateway("click", ok, journal))
    await provider.submit_fiscal(paydoc_id="9001", amount_tiyin=1_000, items=[{"Name": "x"}])
    assert bodies[0] == {
        "service_id": 777,
        "payment_id": 9001,
        "items": [{"Name": "x"}],
        "received_ecash": 1_000,
        "received_cash": 0,
        "received_card": 0,
    }

    def rejected(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error_code": -1, "error_note": "bad"})

    with pytest.raises(ProviderError):
        await ClickProvider(_gateway("click", rejected, journal)).submit_fiscal(paydoc_id="1", amount_tiyin=1, items=[])

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    broken = ClickProvider(_gateway("click", down, journal))
    for _ in range(2):  # 2 × 3 urinish = 6 ketma-ket xato ≥ 5 → ochiq
        with pytest.raises(ProviderError):
            await broken.refund(paydoc_id="9001", amount_tiyin=1, payment_amount_tiyin=1)
    with pytest.raises(ProviderUnavailable):
        await broken.refund(paydoc_id="9001", amount_tiyin=1, payment_amount_tiyin=1)
