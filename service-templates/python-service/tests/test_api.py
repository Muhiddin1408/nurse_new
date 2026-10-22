from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token

from app.core import permissions
from app.routers.internal import ALLOWED_CALLERS


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_user_endpoint_requires_user_jwt(client: TestClient, user_keys, admin_keys) -> None:
    assert client.get("/v1/__SHORT__/whoami").status_code == 401
    uid = uuid.uuid4()
    r = client.get("/v1/__SHORT__/whoami", headers=bearer(mint_user_token(user_keys, user_id=uid)))
    assert r.status_code == 200 and r.json()["user_id"] == str(uid)
    r = client.get("/v1/__SHORT__/whoami", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_admin_endpoint_requires_permission(client: TestClient, admin_keys) -> None:
    ok = mint_admin_token(admin_keys, permissions=[permissions.READ])
    assert client.get("/v1/admin/__SHORT__/whoami", headers=bearer(ok)).status_code == 200
    no = mint_admin_token(admin_keys, permissions=["other.read"])
    assert client.get("/v1/admin/__SHORT__/whoami", headers=bearer(no)).status_code == 403


def test_internal_endpoint(client: TestClient) -> None:
    assert client.get("/internal/v1/__SHORT__/ping").status_code in (401, 403)
    for caller in ALLOWED_CALLERS:
        r = client.get("/internal/v1/__SHORT__/ping", headers={"X-Caller": caller, "X-Internal-Token": "test-token"})
        assert r.status_code == 200
    r = client.get("/internal/v1/__SHORT__/ping", headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"})
    assert r.status_code == 403  # token to'g'ri, lekin ruxsat ro'yxatida yo'q
