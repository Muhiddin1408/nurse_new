"""Bazasiz testlar: health, auth zanjiri (audience, permission), internal API yopiqligi, JWKS."""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_me_requires_user_jwt(client: TestClient, admin_keys) -> None:
    assert client.get("/v1/auth/me").status_code == 401
    r = client.get("/v1/auth/me", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_admin_requires_permission(client: TestClient, admin_keys, user_keys) -> None:
    no = mint_admin_token(admin_keys, permissions=["other.read"])
    r = client.get("/v1/admin/users", headers=bearer(no))
    assert r.status_code == 403 and r.json()["detail"]["permission"] == "users.manage"
    r = client.get("/v1/admin/users", headers=bearer(mint_user_token(user_keys)))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_internal_api_closed(client: TestClient) -> None:
    r = client.get("/internal/v1/auth/ping", headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CALLER_NOT_ALLOWED"


def test_jwks_publishes_signing_key(client: TestClient, user_keys) -> None:
    r = client.get("/v1/auth/.well-known/jwks.json")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    keys = r.json()["keys"]
    assert [k["kid"] for k in keys] == [user_keys.kid]
    assert keys[0]["kty"] == "RSA" and keys[0]["alg"] == "RS256"
    assert base64.urlsafe_b64decode(keys[0]["e"] + "==") == b"\x01\x00\x01"
