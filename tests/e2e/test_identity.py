"""2-bosqich "tayyor" mezoni: login → JWT → boshqa servislarda tekshirish, eventlar servislar orasida."""

from __future__ import annotations

import io

import httpx
from PIL import Image

from conftest import bearer, eventually, random_phone


def test_gateway_hides_internal_api(http: httpx.Client) -> None:
    for path in ("/internal/v1/settings", "/internal/v1/users/x/patients/y", "/internal/v1/files/x/url"):
        assert http.get(path).status_code == 404


def test_login_and_token_accepted_by_every_service(http: httpx.Client, login) -> None:
    session = login()
    h = bearer(session["access"])
    assert session["is_new_user"] is True and session["user"]["roles"] == ["client"]

    me = http.get("/v1/auth/me", headers=h).json()
    assert me["phone"] == session["phone"]
    # user-service, file-service, notification-service — auth-service kalitini fayldan tekshiradi
    assert http.get("/v1/users/me", headers=h).json()["id"] == me["id"]
    assert http.get("/v1/notifications/preferences", headers=h).status_code == 200
    r = http.post("/v1/files/uploads", headers=h, json={"purpose": "avatar", "content_type": "image/png", "size": 10})
    assert r.status_code == 201
    # Soxta token — hamma joyda 401
    for path in ("/v1/users/me", "/v1/notifications/preferences"):
        assert http.get(path, headers=bearer(session["access"][:-4] + "AAAA")).status_code == 401

    # Refresh rotatsiyasi va qayta ishlatishni aniqlash
    r = http.post("/v1/auth/token/refresh", json={"refresh": session["refresh"]})
    assert r.status_code == 200
    reused = http.post("/v1/auth/token/refresh", json={"refresh": session["refresh"]})
    assert reused.status_code == 401 and reused.json()["detail"]["code"] == "REFRESH_REUSED"


def test_registration_event_reaches_user_service(http: httpx.Client, login, admin_token: str) -> None:
    session = login()
    uid = session["user"]["id"]

    def profile_exists() -> None:
        # Admin endpoint — profilni YARATMAYDI (GET /v1/users/me esa yaratadi): faqat event orqali paydo bo'ladi
        r = http.get(f"/v1/admin/users/{uid}/profile", headers=bearer(admin_token))
        assert r.status_code == 200, r.text

    eventually(profile_exists)


def test_avatar_upload_through_s3_and_attach(http: httpx.Client, login) -> None:
    h = bearer(login()["access"])
    image = io.BytesIO()
    Image.new("RGB", (300, 200), (20, 120, 200)).save(image, format="JPEG")
    data = image.getvalue()
    ticket = http.post("/v1/files/uploads", headers=h,
                       json={"purpose": "avatar", "content_type": "image/jpeg", "size": len(data)}).json()  # fmt: skip
    put = httpx.put(ticket["upload_url"], content=data, headers=ticket["headers"])
    assert put.status_code == 200, put.text
    done = http.post(f"/v1/files/{ticket['file_id']}/complete", headers=h).json()
    assert done["status"] == "uploaded" and done["thumbnail_url"]

    me = http.patch("/v1/users/me", headers=h, json={"avatar_file_id": ticket["file_id"], "full_name": "E2E Test"})
    assert me.status_code == 200, me.text  # user-service → file-service internal attach
    avatar = me.json()["avatar_url"]
    assert httpx.get(avatar).status_code == 200  # ommaviy bucket
    assert http.get(f"/v1/files/{ticket['file_id']}", headers=h).json()["status"] == "attached"


def test_admin_block_flows_to_audit(http: httpx.Client, login, admin_token: str) -> None:
    session = login()
    uid = session["user"]["id"]
    ah = bearer(admin_token)
    found = http.get("/v1/admin/users", params={"phone": session["phone"]}, headers=ah).json()
    assert found["total"] == 1 and found["items"][0]["id"] == uid
    r = http.post(f"/v1/admin/users/{uid}/block", json={"reason": "e2e test"}, headers=ah)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert http.post("/v1/auth/token/refresh", json={"refresh": session["refresh"]}).status_code == 401

    def audited() -> None:
        # auth-service → outbox → RabbitMQ → admin-service consumer → audit_log
        items = http.get("/v1/admin/audit", params={"object_id": uid, "action": "user.block"}, headers=ah).json()
        assert items["items"] and items["items"][0]["producer"] == "auth-service"

    eventually(audited)
    assert http.post(f"/v1/admin/users/{uid}/unblock", headers=ah).status_code == 200


def test_settings_versioned(http: httpx.Client, admin_token: str) -> None:
    ah = bearer(admin_token)
    current = http.get("/v1/admin/settings", headers=ah).json()
    waitlist = {**current["values"]["waitlist"], "offer_minutes": current["values"]["waitlist"]["offer_minutes"] % 60 + 1}
    r = http.put("/v1/admin/settings", headers=ah, json={"expected_version": current["version"], "values": {"waitlist": waitlist}})
    assert r.status_code == 200 and r.json()["version"] == current["version"] + 1
    stale = http.put("/v1/admin/settings", headers=ah, json={"expected_version": current["version"], "values": {"waitlist": waitlist}})
    assert stale.status_code == 409


def test_account_deletion_frees_phone(http: httpx.Client, login) -> None:
    session = login()
    phone, uid = session["phone"], session["user"]["id"]
    assert http.delete("/v1/users/me", headers=bearer(session["access"])).status_code == 204

    def deactivated() -> None:
        # user.deletion_requested → auth-service: raqam bo'shatiladi → shu raqam = yangi akkaunt
        r = http.post("/v1/auth/token/refresh", json={"refresh": session["refresh"]})
        assert r.status_code == 401

    eventually(deactivated)
    again = login(phone)
    assert again["is_new_user"] is True and again["user"]["id"] != uid


def test_otp_rate_limit_at_gateway_and_service(http: httpx.Client) -> None:
    phone = random_phone()
    assert http.post("/v1/auth/otp/request", json={"phone": phone}).status_code == 200
    r = http.post("/v1/auth/otp/request", json={"phone": phone})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "OTP_COOLDOWN"
