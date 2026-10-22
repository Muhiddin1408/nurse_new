"""Payout: haftalik hisoblash (min summa, keyingi haftaga o'tish, idempotentlik), mark-paid, CSV."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.events import Events
from medbron_common.testing import assert_event_valid, mint_admin_token

from app.services.payouts import LOCAL_TZ, last_full_week
from tests import builders as b
from tests.fakes import FakeDeps

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def events(db, event_type: str) -> list[dict[str, Any]]:
    rows = db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
    for (payload,) in rows:
        assert_event_valid(payload)
    return [r[0]["data"] for r in rows]


def noon(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=LOCAL_TZ)


@pytest.fixture
def fakes(api: TestClient, run_handler) -> FakeDeps:
    deps: FakeDeps = run_handler.deps
    api.app.state.deps = deps
    return deps


@pytest.fixture
def admin(admin_keys) -> dict[str, str]:
    return bearer(mint_admin_token(admin_keys, permissions=["payouts.manage"]))


def test_weekly_build_min_amount_carry_over_and_idempotency(api, fakes, admin, db, run_handler) -> None:
    start, end = last_full_week()
    other = uuid.uuid4()
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=noon(start), number="MB-A1"))
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=noon(end), number="MB-A2"))
    # Boshqa shifokor: 3 mln (zaklad) — minimal 5 mln dan kam, keyingi haftaga o'tadi
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=noon(end), doctor_id=other, paid=3_000_000))
    # Joriy hafta — bu davrga kirmaydi
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=noon(end + timedelta(days=1))))

    r = api.post("/v1/admin/payouts/build", json={}, headers=admin)
    assert r.status_code == 200, r.text
    [payout] = r.json()
    assert payout["doctor_id"] == str(b.DOCTOR) and payout["bookings_count"] == 2
    assert payout["period_start"] == start.isoformat() and payout["period_end"] == end.isoformat()
    assert (payout["gross_tiyin"], payout["net_tiyin"]) == (30_000_000, 25_200_000)
    assert payout["platform_fee_tiyin"] + payout["provider_fee_tiyin"] + payout["net_tiyin"] == 30_000_000
    [calculated] = events(db, Events.PAYOUT_CALCULATED)
    assert calculated["net_tiyin"] == 25_200_000

    # Ikkinchi build — dublikat yo'q
    assert api.post("/v1/admin/payouts/build", json={}, headers=admin).json() == []
    assert db.scalar("SELECT count(*) FROM payouts") == 1
    assert db.scalar("SELECT count(*) FROM earning_entries WHERE payout_id IS NULL") == 2

    # Minimal summa pasaysa — kichik shifokor ham shu davrga hisoblanadi
    fakes.settings.values["payout"]["min_amount_tiyin"] = 0
    [small] = api.post("/v1/admin/payouts/build", json={"period_end": end.isoformat()}, headers=admin).json()
    assert small["doctor_id"] == str(other) and small["net_tiyin"] == 2_520_000


def test_refund_after_payout_is_deducted_next_week(api, fakes, admin, db, run_handler) -> None:
    last_start, last_end = last_full_week()
    prev_end = last_start - timedelta(days=1)
    first = b.completed(start=noon(prev_end - timedelta(days=2)))
    run_handler(Events.BOOKING_COMPLETED, first)
    [p1] = api.post("/v1/admin/payouts/build", json={"period_end": prev_end.isoformat()}, headers=admin).json()
    assert p1["net_tiyin"] == 12_600_000 and p1["period_end"] == prev_end.isoformat()

    # Nizo bo'yicha qisman refund o'tgan haftada keldi + yangi qabul
    run_handler(Events.REFUND_SUCCEEDED, b.refund(first["booking_id"], 5_000_000, 4_200_000, at=noon(last_start)))
    run_handler(Events.BOOKING_COMPLETED, b.completed(start=noon(last_end)))
    [p2] = api.post("/v1/admin/payouts/build", json={}, headers=admin).json()
    assert p2["refunds_tiyin"] == 4_200_000 and p2["net_tiyin"] == 12_600_000 - 4_200_000
    assert p2["bookings_count"] == 1


def test_negative_balance_is_carried(api, fakes, admin, db, run_handler) -> None:
    last_start, last_end = last_full_week()
    prev_end = last_start - timedelta(days=1)
    paid_out = b.completed(start=noon(prev_end))
    run_handler(Events.BOOKING_COMPLETED, paid_out)
    assert (
        len(api.post("/v1/admin/payouts/build", json={"period_end": prev_end.isoformat()}, headers=admin).json()) == 1
    )
    # To'langan bron bo'yicha keyin to'liq refund — sof manfiy: payout yo'q, qarz keyingi daromaddan ushlanadi
    run_handler(Events.REFUND_SUCCEEDED, b.refund(paid_out["booking_id"], 15_000_000, 12_600_000, at=noon(last_start)))
    fakes.settings.values["payout"]["min_amount_tiyin"] = 0
    assert api.post("/v1/admin/payouts/build", json={}, headers=admin).json() == []
    for _ in range(2):
        run_handler(Events.BOOKING_COMPLETED, b.completed(start=noon(last_end)))
    [p] = api.post("/v1/admin/payouts/build", json={}, headers=admin).json()
    assert (p["gross_tiyin"], p["refunds_tiyin"], p["net_tiyin"]) == (30_000_000, 12_600_000, 12_600_000)


def test_mark_paid_idempotent_with_events_and_statement(api, fakes, admin, db, run_handler, admin_keys) -> None:
    start, _ = last_full_week()
    data = b.completed(start=noon(start), number="MB-CSV1")
    run_handler(Events.BOOKING_COMPLETED, data)
    [p] = api.post("/v1/admin/payouts/build", json={}, headers=admin).json()

    assert (
        api.post(f"/v1/admin/payouts/{p['id']}/mark-paid", json={"bank_reference": " "}, headers=admin).status_code
        == 422
    )
    r = api.post(f"/v1/admin/payouts/{p['id']}/mark-paid", json={"bank_reference": "PP-001"}, headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "paid" and r.json()["bank_reference"] == "PP-001"
    again = api.post(f"/v1/admin/payouts/{p['id']}/mark-paid", json={"bank_reference": "PP-002"}, headers=admin)
    assert again.json()["bank_reference"] == "PP-001"
    [paid] = events(db, Events.PAYOUT_PAID)
    assert paid["net_tiyin"] == 12_600_000 and paid["bank_reference"] == "PP-001"
    audit = [e for e in events(db, Events.AUDIT_RECORDED) if e["action"] == "payout.mark_paid"]
    assert len(audit) == 1

    listed = api.get("/v1/admin/payouts", params={"status": "paid"}, headers=admin).json()
    assert [x["id"] for x in listed] == [p["id"]]
    csv_text = api.get(f"/v1/admin/payouts/{p['id']}/statement", headers=admin).content.decode("utf-8")
    assert (
        csv_text.startswith("﻿")
        and "MB-CSV1;Konsultatsiya;completed;150000.00;22500.00;1500.00;0.00;126000.00" in csv_text
    )

    assert (
        api.post(f"/v1/admin/payouts/{uuid.uuid4()}/mark-paid", json={"bank_reference": "x"}, headers=admin).status_code
        == 404
    )
    reader = bearer(mint_admin_token(admin_keys, permissions=["payments.read"]))
    assert api.get("/v1/admin/payouts", headers=reader).status_code == 403


def test_build_rejects_unfinished_period(api, fakes, admin) -> None:
    today = datetime.now(LOCAL_TZ).date()
    r = api.post("/v1/admin/payouts/build", json={"period_end": today.isoformat()}, headers=admin)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_PERIOD"
