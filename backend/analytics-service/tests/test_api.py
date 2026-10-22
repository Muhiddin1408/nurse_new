"""Health, auth zanjiri, internal API yopiqligi."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token

from app.core import permissions


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_live_and_metrics_without_dependencies(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and ready.json()["checks"]["clickhouse"] != "ok"  # ClickHouse yo'q
    assert "http_requests_total" in client.get("/metrics").text


def test_admin_endpoints_require_permission(client: TestClient, admin_keys, user_keys) -> None:
    for path in ("summary", "bookings/daily", "funnel", "doctors/top"):
        url = f"/v1/admin/analytics/{path}"
        assert client.get(url).status_code == 401
        assert (
            client.get(url, headers=bearer(mint_admin_token(admin_keys, permissions=["bookings.read"]))).status_code
            == 403
        )
        assert client.get(url, headers=bearer(mint_user_token(user_keys, user_id=uuid.uuid4()))).status_code in (
            401,
            403,
        )


def test_internal_api_is_closed(client: TestClient) -> None:
    r = client.get("/internal/v1/x", headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"})
    assert r.status_code in (403, 404)
    assert permissions.ANALYTICS_READ == "analytics.read"


def test_ready_with_clickhouse_and_postgres(api: TestClient) -> None:
    body = api.get("/health/ready").json()
    assert body == {"status": "ok", "checks": {"clickhouse": "ok", "db": "ok"}}
