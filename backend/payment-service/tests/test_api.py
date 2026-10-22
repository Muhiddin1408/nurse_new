"""Health, auth zanjiri, internal API yopiqligi (bazasiz) va webhook himoyasi (jurnal uchun baza bilan)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token

from app.core import permissions


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_user_endpoints_require_user_jwt(client: TestClient, user_keys, admin_keys) -> None:
    assert client.get(f"/v1/payments/{uuid.uuid4()}").status_code == 401
    assert client.post("/v1/payments", json={"booking_id": str(uuid.uuid4()), "provider": "payme"}).status_code == 401
    admin = bearer(mint_admin_token(admin_keys, permissions=["*"]))
    r = client.get("/v1/payments", headers=admin)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_admin_endpoints_require_permission(client: TestClient, admin_keys, user_keys) -> None:
    for path in ("/v1/admin/payments", "/v1/admin/refunds", "/v1/admin/ledger", "/v1/admin/reconciliation"):
        assert client.get(path).status_code == 401
        no = bearer(mint_admin_token(admin_keys, permissions=["bookings.read"]))
        assert client.get(path, headers=no).status_code == 403
    reader = bearer(mint_admin_token(admin_keys, permissions=[permissions.PAYMENTS_READ]))
    r = client.post(f"/v1/admin/refunds/{uuid.uuid4()}/retry", headers=reader)
    assert r.status_code == 403  # o'qish huquqi bilan refund yo'q
    user = bearer(mint_user_token(user_keys, user_id=uuid.uuid4()))
    assert client.get("/v1/admin/payments", headers=user).status_code in (401, 403)


def test_internal_api_is_closed(client: TestClient) -> None:
    r = client.get("/internal/v1/anything", headers={"X-Caller": "booking-service", "X-Internal-Token": "test-token"})
    assert r.status_code in (403, 404)


def test_payme_webhook_rejects_without_basic_auth(api: TestClient) -> None:
    body = {"id": 1, "method": "PerformTransaction", "params": {"id": "x"}}
    r = api.post("/v1/payments/payme/webhook", json=body)
    assert r.status_code == 200  # protokol: har doim 200
    assert r.json()["error"]["code"] == -32504 and r.json()["id"] is None
    r = api.post("/v1/payments/payme/webhook", json=body, headers={"Authorization": "Basic UGF5Y29tOndyb25n"})
    assert r.json()["error"]["code"] == -32504
    r = api.post("/v1/payments/payme/webhook", json=body, headers={"Authorization": "Basic !!!"})
    assert r.json()["error"]["code"] == -32504


def test_click_webhook_rejects_bad_signature(api: TestClient) -> None:
    params = {
        "click_trans_id": "1",
        "service_id": "777",
        "merchant_trans_id": str(uuid.uuid4()),
        "amount": "1000.00",
        "action": "0",
        "sign_time": "2026-10-21 10:00:00",
        "sign_string": "deadbeef",
    }
    r = api.post("/v1/payments/click/prepare", data=params)
    assert r.status_code == 200 and r.json()["error"] == -1
    r = api.post("/v1/payments/click/prepare", data={"action": "0"})
    assert r.json()["error"] == -8
    r = api.post("/v1/payments/click/complete", data=params)  # action=0 complete'ga
    assert r.json()["error"] == -3
