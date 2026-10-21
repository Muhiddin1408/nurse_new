"""Bazasiz: health, auth zanjiri, internal API yopiqligi, sof funksiyalar."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token

from app.services.earnings import resolve_range
from app.services.errors import InvalidPeriod
from app.services.payouts import last_full_week, som


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_doctor_endpoints_require_user_jwt(client: TestClient, user_keys, admin_keys) -> None:
    assert client.get("/v1/doctor/earnings").status_code == 401
    r = client.get("/v1/doctor/payouts", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/doctor/payout-account", headers=bearer(mint_user_token(user_keys)))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_A_DOCTOR"


def test_admin_endpoints_require_permission(client: TestClient, admin_keys, user_keys) -> None:
    assert client.get("/v1/admin/payouts").status_code == 401
    no = mint_admin_token(admin_keys, permissions=["bookings.read"])
    assert client.get("/v1/admin/payouts", headers=bearer(no)).status_code == 403
    assert client.get("/v1/admin/payouts", headers=bearer(mint_user_token(user_keys))).status_code == 403


def test_internal_api_closed(client: TestClient) -> None:
    assert client.get("/internal/v1/billing/ping").status_code in (401, 403)
    r = client.get("/internal/v1/billing/ping", headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"})
    assert r.status_code == 403


def test_week_and_range_helpers() -> None:
    assert last_full_week(date(2026, 10, 21)) == (date(2026, 10, 12), date(2026, 10, 18))
    assert last_full_week(date(2026, 10, 19)) == (date(2026, 10, 12), date(2026, 10, 18))
    assert resolve_range(None, date(2026, 1, 1), date(2026, 1, 31)) == (date(2026, 1, 1), date(2026, 1, 31))
    frm, to = resolve_range("month", None, None)
    assert frm.day == 1 and frm <= to
    with pytest.raises(InvalidPeriod):
        resolve_range(None, None, None)
    assert som(12_500_050) == "125000.50" and som(-5) == "-0.05" and som(0) == "0.00"


def test_masked_number() -> None:
    from app.models import PayoutAccount

    acc = PayoutAccount(doctor_id=uuid.uuid4(), holder_name="A", account_number="8600123412349876")
    assert acc.masked_number == "•••• 9876"
