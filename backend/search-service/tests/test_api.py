"""Bazasiz: health (ES tekshiruvi bilan), admin auth zanjiri, internal API yopiqligi."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_metrics_and_ready_checks_elasticsearch(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert "http_requests_total" in client.get("/metrics").text
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and ready.json()["checks"]["elasticsearch"] != "ok"


def test_admin_reindex_requires_search_manage(client: TestClient, admin_keys, user_keys) -> None:
    assert client.post("/v1/admin/search/reindex").status_code == 401
    other = bearer(mint_admin_token(admin_keys, permissions=["bookings.read"]))
    assert client.post("/v1/admin/search/reindex", headers=other).status_code == 403
    user = bearer(mint_user_token(user_keys, user_id=uuid.uuid4()))
    assert client.get("/v1/admin/search/reindex", headers=user).status_code in (401, 403)


def test_internal_api_is_closed(client: TestClient) -> None:
    r = client.get("/internal/v1/anything", headers={"X-Caller": "catalog-service", "X-Internal-Token": "test-token"})
    assert r.status_code in (403, 404)
