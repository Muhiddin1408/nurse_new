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


def test_auth_chain(client: TestClient, user_keys, admin_keys) -> None:
    assert client.get("/v1/pricing/packages/mine").status_code == 401
    r = client.get("/v1/pricing/packages/mine", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/admin/promo-codes", headers=bearer(mint_user_token(user_keys)))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/admin/promo-codes", headers=bearer(mint_admin_token(admin_keys, permissions=["x"])))
    assert r.status_code == 403 and r.json()["detail"]["permission"] == "pricing.manage"
    r = client.get("/v1/doctor/packages", headers=bearer(mint_user_token(user_keys)))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "ROLE_REQUIRED"


def test_internal_only_booking(client: TestClient) -> None:
    assert client.post("/internal/v1/quotes", json={}).status_code == 401
    r = client.post(
        "/internal/v1/quotes", json={}, headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"}
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CALLER_NOT_ALLOWED"
