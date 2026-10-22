"""Hisobotlar haqiqiy ClickHouse'da: kogorta, kunlik (Toshkent kunlari), filtr, dublikat, oraliq tekshiruvi."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, sample_data

from tests.conftest import Ch, envelope

D1, D2 = uuid.uuid4(), uuid.uuid4()
C1, C2 = uuid.uuid4(), uuid.uuid4()
# 2026-10-20 20:30 UTC = 2026-10-21 01:30 Toshkent
T0 = datetime(2026, 10, 20, 20, 30, tzinfo=UTC)
T1 = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
OUTSIDE = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def money(total: int) -> dict[str, Any]:
    return {"total_tiyin": total, "platform_fee_tiyin": total * 15 // 100, "provider_fee_tiyin": total // 100,
            "doctor_payout_tiyin": total - total * 15 // 100 - total // 100, "discount_tiyin": 0,
            "discount_borne_by": ""}  # fmt: skip


def booking(event_type: str, bid: uuid.UUID, doctor: uuid.UUID, clinic: uuid.UUID | None, at: datetime,
            **data: Any) -> dict[str, Any]:  # fmt: skip
    payload = sample_data(event_type, booking_id=str(bid), doctor_id=str(doctor),
                          clinic_id=str(clinic) if clinic else None, **data)  # fmt: skip
    return envelope(event_type, payload, occurred_at=at)


def payment(event_type: str, bid: uuid.UUID, amount: int, at: datetime) -> dict[str, Any]:
    return envelope(event_type, sample_data(event_type, booking_id=str(bid), amount_tiyin=amount), occurred_at=at)


@pytest.fixture
def seeded(ch: Ch) -> dict[str, uuid.UUID]:
    a, b, c, old = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    created_a = booking("booking.created", a, D1, C1, T0, status="pending_payment", money=money(10_000_000))
    events = [
        created_a,
        payment("payment.captured", a, 10_000_000, T1),
        booking("booking.confirmed", a, D1, C1, T1),
        booking("booking.completed", a, D1, C1, T2, money=money(10_000_000)),
        booking("booking.created", b, D1, C1, T1, status="confirmed", money=money(5_000_000)),
        booking("booking.confirmed", b, D1, C1, T1),
        booking("booking.cancelled", b, D1, C1, T2),
        payment("refund.succeeded", a, 2_000_000, T2),
        booking("booking.created", c, D2, None, T1, status="pending_payment", money=money(3_000_000)),
        booking("booking.expired", c, D2, None, T2),
        booking("booking.created", old, D2, C2, OUTSIDE, status="confirmed", money=money(1)),
    ]
    ch.insert(events)
    ch.insert([created_a])  # at-least-once: o'sha event_id alohida partiyada qayta — hisobotda FINAL bilan bitta
    return {"a": a, "b": b, "c": c}


def admin(admin_keys) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {"Authorization": f"Bearer {mint_admin_token(admin_keys, permissions=['analytics.read'])}"}


RANGE = {"from": "2026-10-21", "to": "2026-10-22"}


def test_summary_totals_rates_and_dedup(api: TestClient, seeded, admin_keys, ch: Ch) -> None:
    assert int(ch.scalar("SELECT count() FROM events_raw FINAL")) == 11
    s = api.get("/v1/admin/analytics/summary", params=RANGE, headers=admin(admin_keys)).json()
    assert (s["created"], s["confirmed"], s["completed"], s["cancelled"], s["expired"]) == (3, 2, 1, 1, 1)
    assert s["gmv_tiyin"] == 10_000_000 and s["captured_tiyin"] == 10_000_000 and s["refunded_tiyin"] == 2_000_000
    assert s["net_payments_tiyin"] == 8_000_000
    assert s["conversion_rate"] == 0.6667 and s["cancellation_rate"] == 0.3333 and s["no_show_rate"] == 0.0


def test_summary_filters_by_doctor_and_clinic(api: TestClient, seeded, admin_keys) -> None:
    h = admin(admin_keys)
    d1 = api.get("/v1/admin/analytics/summary", params={**RANGE, "doctor_id": str(D1)}, headers=h).json()
    assert d1["created"] == 2 and d1["captured_tiyin"] == 10_000_000  # to'lov eventi bron orqali bog'landi
    d2 = api.get("/v1/admin/analytics/summary", params={**RANGE, "doctor_id": str(D2)}, headers=h).json()
    assert (d2["created"], d2["expired"], d2["captured_tiyin"], d2["conversion_rate"]) == (1, 1, 0, 0.0)
    c1 = api.get("/v1/admin/analytics/summary", params={**RANGE, "clinic_id": str(C1)}, headers=h).json()
    assert c1["created"] == 2 and c1["refunded_tiyin"] == 2_000_000


def test_daily_uses_tashkent_days_and_fills_gaps(api: TestClient, seeded, admin_keys) -> None:
    days = api.get("/v1/admin/analytics/bookings/daily", params={"from": "2026-10-20", "to": "2026-10-22"},
                   headers=admin(admin_keys)).json()  # fmt: skip
    assert [d["day"] for d in days] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    assert days[0]["created"] == 0  # 20:30 UTC — Toshkentda ertasi kun
    assert days[1]["created"] == 3 and days[1]["captured_tiyin"] == 10_000_000
    assert days[2]["completed"] == 1 and days[2]["net_payments_tiyin"] == -2_000_000


def test_funnel_and_top_doctors(api: TestClient, seeded, admin_keys) -> None:
    h = admin(admin_keys)
    f = api.get("/v1/admin/analytics/funnel", params=RANGE, headers=h).json()
    assert {k: f[k] for k in ("created", "needs_payment", "paid", "confirmed", "completed", "cancelled", "expired",
                              "no_show")} == {"created": 3, "needs_payment": 2, "paid": 1, "confirmed": 2,
                                              "completed": 1, "cancelled": 1, "expired": 1, "no_show": 0}  # fmt: skip
    top = api.get("/v1/admin/analytics/doctors/top", params={**RANGE, "sort": "gmv"}, headers=h).json()
    assert top[0] == {"doctor_id": str(D1), "completed": 1, "gmv_tiyin": 10_000_000, "no_show": 0, "cancelled": 1}
    only_c2 = api.get("/v1/admin/analytics/doctors/top", params={**RANGE, "clinic_id": str(C2)}, headers=h).json()
    assert only_c2 == []
    bad = api.get("/v1/admin/analytics/doctors/top", params={"sort": "x"}, headers=h)
    assert bad.status_code == 422


def test_range_validation_and_default(api: TestClient, admin_keys) -> None:
    h = admin(admin_keys)
    r = api.get("/v1/admin/analytics/summary", params={"from": "2026-10-22", "to": "2026-10-21"}, headers=h)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_RANGE"
    r = api.get("/v1/admin/analytics/summary", params={"from": "2024-01-01", "to": "2026-01-01"}, headers=h)
    assert r.status_code == 400
    default = api.get("/v1/admin/analytics/bookings/daily", headers=h).json()
    assert len(default) == 30 and default[-1]["day"] >= date(2026, 1, 1).isoformat()


def test_migrations_are_idempotent(ch: Ch) -> None:
    from app.clickhouse.schema import MIGRATIONS, migrate

    assert ch.run(migrate) == []
    assert int(ch.scalar("SELECT count() FROM schema_migrations")) == len(MIGRATIONS)
