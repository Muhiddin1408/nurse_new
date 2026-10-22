"""Bazasiz testlar: health, auth zanjiri, internal API ruxsatlari."""

from __future__ import annotations

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_admin_endpoints_require_admin_token(client: TestClient, user_keys, admin_keys) -> None:
    assert client.get("/v1/admin/admins").status_code == 401
    r = client.get("/v1/admin/admins", headers=bearer(mint_user_token(user_keys)))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/admin/permissions", headers=bearer(mint_admin_token(admin_keys, permissions=["audit.read"])))
    assert r.status_code == 403 and r.json()["detail"]["permission"] == "admins.manage"
    r = client.get("/v1/admin/permissions", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.status_code == 200 and "settings.manage" in r.json()


def test_internal_requires_allowed_caller(client: TestClient) -> None:
    assert client.get("/internal/v1/settings").status_code == 401
    r = client.get("/internal/v1/settings", headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CALLER_NOT_ALLOWED"
    r = client.get("/internal/v1/settings", headers={"X-Caller": "booking-service", "X-Internal-Token": "wrong"})
    assert r.status_code == 401
