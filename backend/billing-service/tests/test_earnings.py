"""Shifokor daromad paneli, payout ro'yxati/CSV, to'lov rekvizitlari, klinika daromadi (access)."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from medbron_common.events import Events
from medbron_common.testing import mint_admin_token, mint_user_token

from app.services.payouts import LOCAL_TZ, last_full_week
from tests import builders as b
from tests.fakes import FakeDeps

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fakes(api: TestClient, run_handler) -> FakeDeps:
    deps: FakeDeps = run_handler.deps
    api.app.state.deps = deps
    return deps


@pytest.fixture
def doctor(user_keys) -> dict[str, str]:
    return bearer(mint_user_token(user_keys, roles=["doctor"], doctor_id=b.DOCTOR))


def test_doctor_earnings_summary_lines_and_payout_status(
    api, fakes, doctor, admin_keys, user_keys, run_handler
) -> None:
    start, end = last_full_week()
    at = datetime.combine(start, time(10), tzinfo=LOCAL_TZ)
    first = b.completed(start=at, number="MB-E1")
    run_handler(Events.BOOKING_COMPLETED, first)
    run_handler(Events.REFUND_SUCCEEDED, b.refund(first["booking_id"], 5_000_000, 4_200_000))
    run_handler(Events.BOOKING_NO_SHOW, b.no_show(start=at + timedelta(days=1), number="MB-E2", paid=3_000_000))
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=at, doctor_id=uuid.uuid4()))  # begona shifokor

    admin = bearer(mint_admin_token(admin_keys, permissions=["payouts.manage"]))
    built = api.post("/v1/admin/payouts/build", json={}, headers=admin).json()
    [p] = [x for x in built if x["doctor_id"] == str(b.DOCTOR)]
    api.post(f"/v1/admin/payouts/{p['id']}/mark-paid", json={"bank_reference": "PP-9"}, headers=admin)

    r = api.get("/v1/doctor/earnings", params={"from": start.isoformat(), "to": end.isoformat()}, headers=doctor)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["bookings_count"] == 2 and s["gross_tiyin"] == 18_000_000 and s["refunds_tiyin"] == 4_200_000
    assert s["net_tiyin"] == 12_600_000 + 2_520_000 - 4_200_000 == s["paid_out_tiyin"]
    assert s["awaiting_payout_tiyin"] == 0
    lines = {line["number"]: line for line in s["transactions"]}
    assert lines["MB-E1"]["net_tiyin"] == 8_400_000 and lines["MB-E1"]["services"] == ["Konsultatsiya"]
    assert lines["MB-E2"]["status"] == "no_show" and lines["MB-E2"]["payout_status"] == "paid"

    week = api.get("/v1/doctor/earnings", params={"period": "today"}, headers=doctor).json()
    assert week["bookings_count"] == 0

    payouts = api.get("/v1/doctor/payouts", headers=doctor).json()
    assert [x["id"] for x in payouts] == [p["id"]]
    csv_r = api.get(f"/v1/doctor/payouts/{p['id']}/statement", headers=doctor)
    assert csv_r.status_code == 200 and "text/csv" in csv_r.headers["content-type"]
    stranger = bearer(mint_user_token(user_keys, roles=["doctor"], doctor_id=uuid.uuid4()))
    assert api.get(f"/v1/doctor/payouts/{p['id']}/statement", headers=stranger).status_code == 404


@pytest.mark.parametrize(
    "params",
    [{}, {"period": "year"}, {"from": "2026-01-10", "to": "2026-01-01"}, {"from": "2024-01-01", "to": "2026-01-01"}],
)
def test_invalid_period(api, fakes, doctor, params) -> None:
    r = api.get("/v1/doctor/earnings", params=params, headers=doctor)
    assert r.status_code in (400, 422)


def test_non_doctor_forbidden(api, fakes, user_keys) -> None:
    client = bearer(mint_user_token(user_keys))
    r = api.get("/v1/doctor/earnings", params={"period": "week"}, headers=client)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_A_DOCTOR"
    assert api.get("/v1/doctor/payouts", headers=client).status_code == 403


def test_payout_account_masked_validated_and_audited(api, fakes, doctor, db) -> None:
    assert api.get("/v1/doctor/payout-account", headers=doctor).status_code == 404
    bad = {"holder_name": "Ali Valiyev", "account_number": "1234"}
    assert api.put("/v1/doctor/payout-account", json=bad, headers=doctor).status_code == 422
    body = {
        "holder_name": "Ali Valiyev",
        "bank_name": "Kapitalbank",
        "account_number": "8600123412341234",
        "mfo": "01018",
    }
    r = api.put("/v1/doctor/payout-account", json=body, headers=doctor)
    assert r.status_code == 200 and r.json()["masked_number"] == "•••• 1234" and "account_number" not in r.json()
    body["account_number"] = "20208000900123456789"
    assert api.put("/v1/doctor/payout-account", json=body, headers=doctor).json()["masked_number"] == "•••• 6789"
    assert api.get("/v1/doctor/payout-account", headers=doctor).json()["bank_name"] == "Kapitalbank"
    audits = db.all("SELECT payload FROM outbox_events WHERE event_type = 'audit.recorded' ORDER BY id")
    assert len(audits) == 2
    assert "8600123412341234" not in str(audits) and "20208000900123456789" not in str(audits)


def test_clinic_earnings_require_clinic_admin(api, fakes, user_keys, run_handler) -> None:
    start, _ = last_full_week()
    at = datetime.combine(start, time(10), tzinfo=LOCAL_TZ)
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=at, clinic_id=b.CLINIC))
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=at))  # klinikasiz (uy chaqiruvi)
    admin_id, member_id = uuid.uuid4(), uuid.uuid4()
    fakes.catalog.members[(admin_id, b.CLINIC)] = "clinic_admin"
    fakes.catalog.members[(member_id, b.CLINIC)] = "doctor"
    params = {"from": start.isoformat(), "to": (start + timedelta(days=6)).isoformat()}
    url = f"/v1/clinic/{b.CLINIC}/earnings"

    r = api.get(
        url, params=params, headers=bearer(mint_user_token(user_keys, user_id=admin_id, roles=["clinic_admin"]))
    )
    assert r.status_code == 200 and r.json()["bookings_count"] == 1
    assert r.json()["transactions"][0]["doctor_id"] == str(b.DOCTOR)
    calls = fakes.catalog.calls
    api.get(url, params=params, headers=bearer(mint_user_token(user_keys, user_id=admin_id, roles=["clinic_admin"])))
    assert fakes.catalog.calls == calls  # Redis keshi

    for uid in (member_id, uuid.uuid4()):
        r = api.get(url, params=params, headers=bearer(mint_user_token(user_keys, user_id=uid)))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "CLINIC_NOT_FOUND"
