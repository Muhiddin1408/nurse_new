"""Kirish oqimi haqiqiy Postgres + Redis bilan: OTP → token → refresh rotatsiyasi → chiqish,
rate-limit, brute-force bloki, admin bloklashi. Docker talab qiladi: `pytest -m integration`."""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from medbron_common.phone import normalize_uz_phone
from medbron_common.sms import SmsSendError
from medbron_common.testing import assert_event_valid, mint_admin_token

pytestmark = pytest.mark.integration

PHONE = "+998901112233"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def login(api: TestClient, clear_cooldown):
    def do(phone: str = PHONE) -> dict[str, Any]:
        clear_cooldown(normalize_uz_phone(phone))
        r = api.post("/v1/auth/otp/request", json={"phone": phone})
        assert r.status_code == 200, r.text
        code = r.json()["debug_code"]
        r = api.post("/v1/auth/otp/verify", json={"phone": phone, "code": code})
        assert r.status_code == 200, r.text
        return dict(r.json())

    return do


def outbox(db, event_type: str) -> list[dict[str, Any]]:
    return [
        row[0] for row in db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
    ]


def decode(token: str, user_keys) -> dict[str, Any]:
    return jwt.decode(token, user_keys.public_pem, algorithms=["RS256"], audience="medbron", issuer="medbron-auth")


def test_login_creates_user_once_and_issues_valid_tokens(api: TestClient, login, db, user_keys) -> None:
    body = login("+998 90 111-22-33")
    assert body["is_new_user"] is True
    assert body["tokens"] == {"access": body["access"], "refresh": body["refresh"]}
    assert body["user"]["roles"] == ["client"] and body["user"]["active_role"] == "client"

    header = jwt.get_unverified_header(body["access"])
    assert header["kid"] == user_keys.kid and header["alg"] == "RS256"
    claims = decode(body["access"], user_keys)
    assert claims["sub"] == body["user"]["id"] and claims["phone"] == PHONE
    assert claims["role"] == "client" and claims["roles"] == ["client"] and claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 900 and "doctor_id" not in claims

    events = outbox(db, "user.registered")
    assert len(events) == 1
    assert_event_valid(events[0])
    assert events[0]["data"]["phone"] == PHONE and events[0]["aggregate_id"] == body["user"]["id"]

    # OTP ochiq saqlanmaydi
    stored = db.scalar("SELECT code_hash FROM otp_codes LIMIT 1")
    assert len(stored) == 64

    again = login()
    assert again["is_new_user"] is False and again["user"]["id"] == body["user"]["id"]
    assert len(outbox(db, "user.registered")) == 1

    me = api.get("/v1/auth/me", headers=bearer(again["access"])).json()
    assert me["id"] == body["user"]["id"] and me["phone"] == PHONE and me["roles"] == ["client"]


def test_invalid_phone_and_cooldown(api: TestClient) -> None:
    r = api.post("/v1/auth/otp/verify", json={"phone": PHONE, "code": "12ab"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "VALIDATION_ERROR"
    r = api.post("/v1/auth/otp/request", json={"phone": "+79001234567"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_PHONE"
    assert api.post("/v1/auth/otp/request", json={"phone": PHONE}).status_code == 200
    r = api.post("/v1/auth/otp/request", json={"phone": PHONE})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "OTP_COOLDOWN"
    assert int(r.headers["Retry-After"]) >= 1


def test_wrong_codes_exhaust_attempts_then_block(api: TestClient, db) -> None:
    r = api.post("/v1/auth/otp/request", json={"phone": PHONE})
    good = r.json()["debug_code"]
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(5):
        r = api.post("/v1/auth/otp/verify", json={"phone": PHONE, "code": wrong})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_CODE"
    assert db.scalar("SELECT attempts FROM otp_codes WHERE phone = :p", p=PHONE) == 5
    # 6-urinish — to'g'ri kod bilan ham: kod yopilgan
    r = api.post("/v1/auth/otp/verify", json={"phone": PHONE, "code": good})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "OTP_ATTEMPTS_EXCEEDED"

    # Raqam bo'yicha soatiga 10 ta xato — keyin blok (yangi kodlar bilan ham)
    for _ in range(5):
        r = api.post("/v1/auth/otp/verify", json={"phone": PHONE, "code": wrong})
        assert r.status_code == 400
    r = api.post("/v1/auth/otp/verify", json={"phone": PHONE, "code": wrong})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "OTP_VERIFY_BLOCKED"


def test_sms_failure_is_fail_closed_and_releases_limits(api: TestClient, db) -> None:
    class Down:
        async def send(self, phone: str, text: str) -> str:
            raise SmsSendError("provider down")

        async def aclose(self) -> None:
            return None

    real = api.app.state.sms
    api.app.state.sms = Down()
    r = api.post("/v1/auth/otp/request", json={"phone": PHONE})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "SMS_UNAVAILABLE"
    assert db.scalar("SELECT count(*) FROM otp_codes") == 0
    api.app.state.sms = real
    # cooldown sarflanmagan — darhol qayta so'rash mumkin
    assert api.post("/v1/auth/otp/request", json={"phone": PHONE}).status_code == 200


def test_otp_text_goes_to_provider(api: TestClient) -> None:
    sms = api.app.state.sms
    r = api.post("/v1/auth/otp/request", json={"phone": PHONE, "language": "ru"})
    phone, text = sms.sent[-1]
    assert phone == PHONE and r.json()["debug_code"] in text and text.startswith("Код")


def test_refresh_rotation_and_reuse_detection(api: TestClient, login) -> None:
    first = login()
    r = api.post("/v1/auth/token/refresh", json={"refresh": first["refresh"]})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh"] != first["refresh"]

    # Eski (rotatsiya qilingan) token qayta keldi → butun sessiya yopiladi
    r = api.post("/v1/auth/token/refresh", json={"refresh": first["refresh"]})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "REFRESH_REUSED"
    r = api.post("/v1/auth/token/refresh", json={"refresh": second["refresh"]})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "REFRESH_INVALID"

    r = api.post("/v1/auth/token/refresh", json={"refresh": "x" * 43})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "REFRESH_INVALID"


def test_logout_and_logout_all(api: TestClient, login) -> None:
    a = login()
    other = login("+998935556677")
    # Begona refresh bilan chiqib bo'lmaydi
    r = api.post("/v1/auth/logout", json={"refresh": other["refresh"]}, headers=bearer(a["access"]))
    assert r.status_code == 401
    r = api.post("/v1/auth/logout", json={"refresh": a["refresh"]}, headers=bearer(a["access"]))
    assert r.status_code == 204
    assert api.post("/v1/auth/token/refresh", json={"refresh": a["refresh"]}).status_code == 401

    b = login()
    c = login()
    assert api.post("/v1/auth/logout/all", headers=bearer(b["access"])).status_code == 204
    for session in (b, c):
        assert api.post("/v1/auth/token/refresh", json={"refresh": session["refresh"]}).status_code == 401
    assert api.post("/v1/auth/token/refresh", json={"refresh": other["refresh"]}).status_code == 200


def test_switch_role_requires_available_role(api: TestClient, login, db, user_keys, run_handler) -> None:
    a = login()
    r = api.post("/v1/auth/switch-role", json={"refresh": a["refresh"], "role": "doctor"}, headers=bearer(a["access"]))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "ROLE_NOT_AVAILABLE"

    doctor_id = uuid.uuid4()
    run_handler(
        "doctor.approved",
        {
            "doctor_id": str(doctor_id),
            "user_id": a["user"]["id"],
            "full_name": "Dr",
            "specialization_ids": [],
            "accepts_home_visits": False,
            "approved_by": str(uuid.uuid4()),
            "approved_at": "2026-10-21T08:00:00Z",
        },
    )
    r = api.post("/v1/auth/switch-role", json={"refresh": a["refresh"], "role": "doctor"}, headers=bearer(a["access"]))
    assert r.status_code == 200, r.text
    claims = decode(r.json()["access"], user_keys)
    assert claims["role"] == "doctor" and set(claims["roles"]) == {"client", "doctor"}
    assert claims["doctor_id"] == str(doctor_id)
    # Keyingi kirishda tanlangan rol eslab qolinadi
    assert login()["user"]["active_role"] == "doctor"


def test_admin_block_and_unblock(api: TestClient, login, db, admin_keys, clear_cooldown) -> None:
    user = login()
    admin_id = uuid.uuid4()
    token = mint_admin_token(admin_keys, admin_id=admin_id, permissions=["users.manage"])
    uid = user["user"]["id"]

    listed = api.get("/v1/admin/users", params={"phone": "998901112233"}, headers=bearer(token)).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == uid

    r = api.post(f"/v1/admin/users/{uid}/block", json={"reason": "firibgarlik"}, headers=bearer(token))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert api.post("/v1/auth/token/refresh", json={"refresh": user["refresh"]}).status_code == 401
    r = api.post("/v1/auth/otp/request", json={"phone": PHONE})
    assert r.status_code == 429  # cooldown — shuning uchun tozalab, qayta so'raymiz
    clear_cooldown(PHONE)
    r = api.post("/v1/auth/otp/request", json={"phone": PHONE})
    r = api.post("/v1/auth/otp/verify", json={"phone": PHONE, "code": r.json()["debug_code"]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "ACCOUNT_BLOCKED"
    assert api.post(f"/v1/admin/users/{uid}/block", json={"reason": "yana"}, headers=bearer(token)).status_code == 409

    deactivated = outbox(db, "user.deactivated")
    assert [e["data"] for e in deactivated] == [{"user_id": uid, "reason": "blocked_by_admin"}]
    audit = outbox(db, "audit.recorded")
    assert audit[0]["data"]["action"] == "user.block" and audit[0]["data"]["actor_id"] == str(admin_id)
    for event in (*deactivated, *audit):
        assert_event_valid(event)

    r = api.post(f"/v1/admin/users/{uid}/unblock", headers=bearer(token))
    assert r.status_code == 200 and r.json()["is_active"] is True
    reactivated = outbox(db, "user.reactivated")
    assert reactivated[0]["data"]["reactivated_by"] == str(admin_id)
    assert_event_valid(reactivated[0])
    assert login()["user"]["id"] == uid

    missing = uuid.uuid4()
    assert (
        api.post(f"/v1/admin/users/{missing}/block", json={"reason": "x" * 5}, headers=bearer(token)).status_code == 404
    )
