"""Chiquvchi HTTP kontraktlari: booking `/summary` (contracts/openapi/booking-service.internal.yaml) va
file-service `/attach`, `/url` — yo'llar, sarlavhalar, xato tarjimasi."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from medbron_common.http_client import ServiceClient, UpstreamError

from app.clients.upstream import BookingClient, FileClient, FileRejected


def wire(client, handler) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def recorder(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client.http = ServiceClient(
        client.target,
        "http://upstream",
        caller="dispute-service",
        token="tok",
        transport=httpx.MockTransport(recorder),
        backoff_base=0,
    )
    return seen


def error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": {"code": code, "message": "m"}})


async def test_booking_summary() -> None:
    booking_id, user_id = uuid.uuid4(), uuid.uuid4()
    body = {"booking_id": str(booking_id), "number": "MB-1", "status": "completed", "client_id": str(user_id),
            "doctor_id": str(uuid.uuid4()), "start_at": "2026-10-20T05:00:00+00:00", "paid_tiyin": 100,
            "total_tiyin": 100}  # fmt: skip
    client = BookingClient("http://x")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("user_id") == str(user_id):
            return httpx.Response(200, json=body)
        return error(404, "BOOKING_NOT_FOUND")

    seen = wire(client, handler)
    assert (await client.summary(booking_id, user_id)) == body
    assert await client.summary(booking_id, uuid.uuid4()) is None
    assert seen[0].url.path == f"/internal/v1/bookings/{booking_id}/summary"
    assert seen[0].headers["x-caller"] == "dispute-service" and seen[0].headers["x-internal-token"] == "tok"
    wire(client, lambda r: error(400, "BAD"))
    with pytest.raises(UpstreamError):
        await client.summary(booking_id, user_id)
    await client.aclose()


async def test_file_attach_and_url() -> None:
    file_id, owner = uuid.uuid4(), uuid.uuid4()
    client = FileClient("http://x")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/attach"):
            body = json.loads(request.content)
            if body["owner_user_id"] != str(owner):
                return error(404, "FILE_NOT_FOUND")
            return httpx.Response(200, json={"id": str(file_id), "status": "attached", "content_type": "image/png"})
        return httpx.Response(200, json={"url": "https://s3/x", "expires_in": 60})

    seen = wire(client, handler)
    out = await client.attach(file_id, owner_user_id=owner, owner_ref="dispute:1")
    assert out["content_type"] == "image/png"
    assert json.loads(seen[0].content) == {
        "owner_user_id": str(owner),
        "owner_ref": "dispute:1",
        "purpose": "dispute_evidence",
    }
    with pytest.raises(FileRejected):
        await client.attach(file_id, owner_user_id=uuid.uuid4(), owner_ref="dispute:1")
    assert await client.url(file_id, viewer_type="admin", viewer_id="a", ttl=60) == "https://s3/x"
    assert seen[-1].url.params["viewer_type"] == "admin" and seen[-1].url.path == f"/internal/v1/files/{file_id}/url"
    wire(client, lambda r: error(500, "BOOM"))
    with pytest.raises(UpstreamError):
        await client.attach(file_id, owner_user_id=owner, owner_ref="dispute:1")
    await client.aclose()
