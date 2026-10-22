"""Bazasiz: health, auth zanjiri (user / admin + permission), internal API yopiqligi."""

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


def test_user_endpoints_require_user_jwt(client: TestClient, admin_keys) -> None:
    assert client.get("/v1/disputes/mine").status_code == 401
    body = {"booking_id": str(uuid.uuid4()), "reason": "other"}
    assert client.post("/v1/disputes", json=body).status_code == 401
    admin = bearer(mint_admin_token(admin_keys, permissions=["*"]))
    r = client.get("/v1/disputes/mine", headers=admin)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_admin_endpoints_require_permission(client: TestClient, admin_keys, user_keys) -> None:
    assert client.get("/v1/admin/disputes").status_code == 401
    other = bearer(mint_admin_token(admin_keys, permissions=["payments.read"]))
    assert client.get("/v1/admin/disputes", headers=other).status_code == 403
    r = client.post(f"/v1/admin/disputes/{uuid.uuid4()}/resolve", headers=other, json={})
    assert r.status_code == 403
    user = bearer(mint_user_token(user_keys, user_id=uuid.uuid4()))
    assert client.get("/v1/admin/disputes", headers=user).status_code in (401, 403)


def test_internal_api_is_closed(client: TestClient) -> None:
    r = client.get("/internal/v1/anything", headers={"X-Caller": "booking-service", "X-Internal-Token": "test-token"})
    assert r.status_code in (403, 404)
