"""Upstream client'larning HTTP kontrakti (contracts/openapi/*.internal.yaml): yo'llar, sarlavhalar,
idempotency kalitlari, xato tarjimasi."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from medbron_common.http_client import ServiceClient

from app.clients.upstream import CatalogClient, PricingClient, ScheduleClient, UpstreamRejected, UserClient

SLOT = {
    "id": str(uuid.uuid4()),
    "doctor_id": str(uuid.uuid4()),
    "clinic_id": None,
    "room_id": None,
    "place": "home",
    "start_at": "2026-10-25T05:00:00+00:00",
    "end_at": "2026-10-25T06:00:00+00:00",
    "duration_minutes": 60,
    "status": "held",
    "hold_ref": "x",
    "hold_expires_at": "2026-10-21T08:10:00+00:00",
    "price_adjust_percent": -10,
}


def wire(client, handler) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def recorder(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client.http = ServiceClient(
        client.target,
        "http://upstream",
        caller="booking-service",
        token="tok",
        transport=httpx.MockTransport(recorder),
        backoff_base=0,
    )
    return seen


def error(status: int, code: str, **extra: object) -> httpx.Response:
    return httpx.Response(status, json={"detail": {"code": code, "message": "m", **extra}})


async def test_schedule_client() -> None:
    client = ScheduleClient("http://x")
    responses = {"hold": httpx.Response(200, json=SLOT)}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/hold"):
            return responses["hold"]
        if request.url.path.endswith("/confirm"):
            return error(409, "HOLD_LOST")
        if request.url.path.endswith("/release"):
            return httpx.Response(204)
        if "free-slots" in request.url.path:
            return httpx.Response(200, json={"items": [SLOT]})
        return error(404, "SLOT_NOT_FOUND")

    seen = wire(client, handler)
    ref, sid = uuid.uuid4(), uuid.uuid4()
    slot = await client.hold(sid, hold_ref=ref, doctor_id=uuid.uuid4(), ttl_seconds=600, travel_buffer_minutes=25)
    assert slot.place == "home" and slot.price_adjust_percent == -10 and slot.clinic_id is None
    body = json.loads(seen[-1].content)
    assert body["hold_ref"] == str(ref) and body["travel_buffer_minutes"] == 25
    assert (
        seen[-1].headers["idempotency-key"] == f"hold:{sid}:{ref}" and seen[-1].headers["x-caller"] == "booking-service"
    )
    with pytest.raises(UpstreamRejected) as exc:
        await client.confirm(sid, ref)
    assert exc.value.code == "HOLD_LOST"
    await client.release(sid, ref)
    assert await client.slot(sid) is None
    responses["hold"] = error(409, "SLOT_TAKEN")
    with pytest.raises(UpstreamRejected):
        await client.hold(sid, hold_ref=ref, doctor_id=uuid.uuid4(), ttl_seconds=600, travel_buffer_minutes=None)
    found = await client.free_slots(
        uuid.uuid4(), start=datetime.now(UTC), end=datetime.now(UTC), place="home", min_minutes=30, limit=3
    )
    assert len(found) == 1 and "place=home" in str(seen[-1].url)
    await client.aclose()


async def test_pricing_catalog_user_clients() -> None:
    pricing = PricingClient("http://x")

    def pricing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commit"):
            return error(409, "PROMO_LIMIT_REACHED")
        return error(422, "PROMO_INVALID", reason="expired")

    wire(pricing, pricing_handler)
    with pytest.raises(UpstreamRejected) as exc:
        await pricing.quote({"client_id": "c", "slot_start_at": "t"})
    assert exc.value.code == "PROMO_INVALID" and exc.value.detail["reason"] == "expired"
    with pytest.raises(UpstreamRejected):
        await pricing.commit("q", booking_id=uuid.uuid4(), client_id=uuid.uuid4())

    catalog = CatalogClient("http://x")
    seen = wire(
        catalog,
        lambda r: (
            httpx.Response(200, json={"items": []})
            if "services" in r.url.path
            else (
                error(404, "DOCTOR_NOT_FOUND")
                if "bookability" in r.url.path
                else httpx.Response(200, json={"allowed": True, "role": "clinic_admin", "clinic_ids": []})
            )
        ),
    )
    doctor = uuid.uuid4()
    assert await catalog.bookability(doctor) is None
    await catalog.bookable_services(doctor, [uuid.uuid4(), uuid.uuid4()])
    assert f"bookable_for={doctor}" in str(seen[-1].url) and "%2C" in str(seen[-1].url)
    assert (await catalog.access(uuid.uuid4(), clinic_id=uuid.uuid4()))["allowed"] is True

    users = UserClient("http://x")
    wire(users, lambda r: error(404, "PATIENT_NOT_FOUND"))
    assert await users.patient(uuid.uuid4(), uuid.uuid4()) is None
    assert await users.address(uuid.uuid4(), uuid.uuid4()) is None
    for c in (pricing, catalog, users):
        await c.aclose()
