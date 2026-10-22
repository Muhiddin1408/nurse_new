"""Bazasiz testlar: health, auth zanjiri, sof qoidalar (bekor qilish siyosati, C4, C6, raqam)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token

from app.services import policy

POLICY = {"full_refund_hours": 24, "partial_refund_hours": 2, "partial_refund_percent": 50}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_auth(client: TestClient, user_keys, admin_keys) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/v1/bookings/mine").status_code == 401
    r = client.get("/v1/bookings/mine", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/admin/bookings", headers=bearer(mint_admin_token(admin_keys, permissions=["x"])))
    assert r.status_code == 403
    r = client.get(
        f"/internal/v1/bookings/{uuid.uuid4()}/payable",
        headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"},
    )
    assert r.status_code == 403


def test_cancellation_policy_table() -> None:
    now = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)

    def ev(status: str, hours: float, actor: str = "client", paid: int = 10_000_000) -> tuple:
        o = policy.evaluate_cancellation(
            status=status, start_at=now + timedelta(hours=hours), paid_tiyin=paid, actor=actor, now=now, policy=POLICY
        )
        return o.allowed, o.refund_tiyin, o.penalty_tiyin, o.reason_code

    assert ev("pending_payment", 1) == (True, 0, 0, "unpaid")
    assert ev("completed", 48) == (False, 0, 0, "final_state")
    assert ev("confirmed", -1) == (False, 0, 0, "already_started")
    assert ev("confirmed", 1, actor="doctor") == (True, 10_000_000, 0, "doctor_cancelled")
    assert ev("confirmed", 25) == (True, 10_000_000, 0, "full_refund")
    assert ev("confirmed", 24) == (True, 5_000_000, 5_000_000, "partial_refund")  # aynan 24 soat — qisman
    assert ev("confirmed", 3, paid=5) == (True, 3, 2, "partial_refund")  # 2.5 → 3 (half-up)
    assert ev("confirmed", 1) == (True, 0, 10_000_000, "late_cancellation")


def test_travel_buffer_patient_rules_and_number() -> None:
    assert policy.travel_buffer_minutes(None) == 30
    assert policy.travel_buffer_minutes(0) == 15 and policy.travel_buffer_minutes(5) == 25
    assert policy.travel_buffer_minutes(100) == 90
    assert policy.distance_km(None, {"latitude": "41", "longitude": "69"}) is None
    km = policy.distance_km({"lat": 41.3111, "lng": 69.2797}, {"latitude": "41.32", "longitude": "69.29"})
    assert km is not None and 1 < km < 2
    adult = {
        "is_pediatric": False,
        "accepts_children": False,
        "min_patient_age": None,
        "max_patient_age": None,
        "allowed_gender": "",
    }
    today = date(2026, 10, 21)
    assert policy.patient_fit_error(adult, date(1990, 1, 1), "male", today) is None
    assert "bolalarni" in policy.patient_fit_error(adult, date(2015, 1, 1), "male", today)
    assert "faqat bolalarni" in policy.patient_fit_error(
        {**adult, "is_pediatric": True}, date(1990, 1, 1), "male", today
    )
    assert policy.patient_fit_error({**adult, "min_patient_age": 40}, date(1990, 1, 1), "male", today)
    assert policy.patient_fit_error({**adult, "max_patient_age": 30}, date(1990, 1, 1), "male", today)
    assert policy.patient_fit_error({**adult, "allowed_gender": "female"}, date(1990, 1, 1), "male", today)
    assert policy.age_on(date(2000, 10, 22), today) == 25
    number = policy.booking_number(datetime(2026, 10, 20, 20, 0, tzinfo=UTC))  # 01:00 Toshkent — 21-oktabr
    assert number.startswith("MB-261021-") and len(number) == 14
    assert not set(number[-4:]) & set("01OI")
    assert policy.request_hash({"a": 1, "b": [2]}) == policy.request_hash({"b": [2], "a": 1})
