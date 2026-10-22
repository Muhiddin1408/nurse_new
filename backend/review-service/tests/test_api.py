"""Bazasiz: health, auth zanjiri, internal API yopiqligi."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_auth_required(client: TestClient, user_keys, admin_keys) -> None:
    assert client.post("/v1/reviews", json={}).status_code == 401
    assert client.get("/v1/reviews/mine").status_code == 401
    assert client.post(f"/v1/doctor/reviews/{uuid.uuid4()}/reply", json={"text": "x"}).status_code == 401
    assert client.get("/v1/admin/reviews").status_code == 401
    no = bearer(mint_admin_token(admin_keys, permissions=["bookings.read"]))
    assert client.get("/v1/admin/reviews", headers=no).status_code == 403
    user = bearer(mint_user_token(user_keys, user_id=uuid.uuid4()))
    assert client.get("/v1/admin/reviews", headers=user).status_code in (401, 403)


def test_internal_api_is_closed(client: TestClient) -> None:
    r = client.get("/internal/v1/x", headers={"X-Caller": "booking-service", "X-Internal-Token": "test-token"})
    assert r.status_code in (403, 404)
