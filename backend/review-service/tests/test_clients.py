"""booking internal API kontrakti (contracts/openapi/booking-service.internal.yaml: review-eligibility)."""

from __future__ import annotations

import uuid

import httpx

from medbron_common.http_client import ServiceClient

from app.clients.upstream import BookingClient


async def test_review_eligibility_contract() -> None:
    booking_id, client_id, doctor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "eligible": True, "reason": "", "doctor_id": str(doctor_id), "completed_at": "2026-10-20T10:00:00+00:00",
        })  # fmt: skip

    client = BookingClient("http://x")
    client.http = ServiceClient(
        client.target, "http://upstream", caller="review-service", token="tok",
        transport=httpx.MockTransport(handler), backoff_base=0,
    )  # fmt: skip
    e = await client.review_eligibility(booking_id, client_id)
    assert e.eligible and e.doctor_id == doctor_id and e.completed_at is not None
    assert seen[0].url.path == f"/internal/v1/bookings/{booking_id}/review-eligibility"
    assert seen[0].url.params["client_id"] == str(client_id)
    assert seen[0].headers["x-caller"] == "review-service" and seen[0].headers["x-internal-token"] == "tok"
    await client.aclose()
