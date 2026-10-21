"""Bazasiz testlar: health, auth zanjiri, internal API ruxsatlari."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_user_endpoints_require_user_jwt(client: TestClient, admin_keys) -> None:
    assert client.get("/v1/users/me").status_code == 401
    r = client.get("/v1/users/me", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_internal_only_booking(client: TestClient) -> None:
    path = f"/internal/v1/users/{uuid.uuid4()}/patients/{uuid.uuid4()}"
    assert client.get(path).status_code == 401
    r = client.get(path, headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CALLER_NOT_ALLOWED"
