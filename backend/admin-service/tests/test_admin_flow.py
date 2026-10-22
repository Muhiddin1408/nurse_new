"""Admin kirishi (parol → TOTP), adminlar/rollar, global sozlamalar, audit — haqiqiy Postgres + Redis.
Docker talab qiladi: `pytest -m integration`."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import pyotp
import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import assert_event_valid, mint_admin_token

pytestmark = pytest.mark.integration

PASSWORD = "Str0ng-Passw0rd!"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def secret_of(uri: str) -> str:
    return parse_qs(urlparse(uri).query)["secret"][0]


def bootstrap(login: str = "ops@medbron.uz", password: str = PASSWORD) -> str:
    """CLI yo'li (`python -m app.cli create-admin`) — birinchi admin. Qaytaradi: TOTP sir."""
    from app.cli import create_admin
    from app.core.database import get_engine

    async def run() -> str:
        try:
            return await create_admin(login, "Ops", "superadmin", password)
        finally:
            await get_engine().dispose()

    return secret_of(asyncio.run(run()))


def code_for(secret: str, offset_steps: int = 0) -> str:
    totp = pyotp.TOTP(secret)
    return totp.at(time.time() + offset_steps * totp.interval)


def login(api: TestClient, secret: str, login_name: str = "ops@medbron.uz", password: str = PASSWORD) -> dict:
    """Bir testda bir necha kirish: TOTP replay himoyasi bir 30 s qadamdagi kodni qayta qabul qilmaydi,
    shuning uchun test oxirgi qadam belgisini tozalaydi (prod'da admin keyingi kodni kutadi)."""
    from tests.conftest import Db

    Db(api.app.state.test_db_url).scalar("UPDATE admins SET totp_last_step = NULL WHERE login = :l", l=login_name)
    r = api.post("/v1/admin/auth/login", json={"login": login_name, "password": password})
    assert r.status_code == 200, r.text
    r = api.post("/v1/admin/auth/totp", json={"mfa_token": r.json()["mfa_token"], "code": code_for(secret)})
    assert r.status_code == 200, r.text
    return dict(r.json())


def outbox(db, event_type: str) -> list[dict[str, Any]]:
    rows = db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
    return [row[0] for row in rows]


def test_password_then_totp_issues_admin_token(api: TestClient, db, admin_keys) -> None:
    secret = bootstrap()
    r = api.post("/v1/admin/auth/login", json={"login": "OPS@medbron.uz ", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["next"] == "totp"
    mfa = r.json()["mfa_token"]
    r = api.post("/v1/admin/auth/totp", json={"mfa_token": mfa, "code": "000000"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "MFA_INVALID"
    code = code_for(secret)
    r = api.post("/v1/admin/auth/totp", json={"mfa_token": mfa, "code": code})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["permissions"] == ["*"] and body["expires_in"] == 1800
    claims = jwt.decode(body["access"], admin_keys.public_pem, algorithms=["RS256"], audience="admin")
    assert claims["iss"] == "medbron-admin" and claims["permissions"] == ["*"] and claims["sub"] == body["admin"]["id"]
    assert jwt.get_unverified_header(body["access"])["kid"] == admin_keys.kid

    # MFA token bir martalik; o'sha TOTP kodi ham qayta ishlamaydi (replay)
    assert api.post("/v1/admin/auth/totp", json={"mfa_token": mfa, "code": code}).json()["detail"]["code"] == (
        "MFA_EXPIRED"
    )
    r = api.post("/v1/admin/auth/login", json={"login": "ops@medbron.uz", "password": PASSWORD})
    r = api.post("/v1/admin/auth/totp", json={"mfa_token": r.json()["mfa_token"], "code": code})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "MFA_INVALID"

    me = api.get("/v1/admin/auth/me", headers=bearer(body["access"])).json()
    assert me["login"] == "ops@medbron.uz" and me["permissions"] == ["*"]
    logins = [e for e in outbox(db, "audit.recorded") if e["data"]["action"] == "admin.login"]
    assert len(logins) == 1 and logins[0]["data"]["actor_type"] == "admin"
    assert db.scalar("SELECT totp_secret_enc FROM admins") != secret  # bazada shifrlangan


def test_lockout_after_failed_passwords(api: TestClient) -> None:
    bootstrap()
    for _ in range(5):
        r = api.post("/v1/admin/auth/login", json={"login": "ops@medbron.uz", "password": "wrong"})
        assert r.status_code == 401 and r.json()["detail"]["code"] == "INVALID_CREDENTIALS"
    r = api.post("/v1/admin/auth/login", json={"login": "ops@medbron.uz", "password": PASSWORD})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "LOGIN_LOCKED"
    # Noma'lum login — xuddi shu javob (login mavjudligi oshkor qilinmaydi)
    r = api.post("/v1/admin/auth/login", json={"login": "nobody", "password": "x"})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_migrated_django_password_is_rehashed(api: TestClient, db) -> None:
    secret = bootstrap(password="Temp0rary-Passw0rd")
    salt, iterations = "s4lt", 870000
    digest = base64.b64encode(hashlib.pbkdf2_hmac("sha256", b"Legacy-Passw0rd", salt.encode(), iterations)).decode()
    db.scalar("UPDATE admins SET password_hash = :h", h=f"pbkdf2_sha256${iterations}${salt}${digest}")
    login(api, secret, password="Legacy-Passw0rd")
    assert db.scalar("SELECT password_hash FROM admins").startswith("$argon2id$")
    login(api, secret, password="Legacy-Passw0rd")


def test_admins_and_roles_management(api: TestClient, db) -> None:
    secret = bootstrap()
    token = login(api, secret)["access"]
    h = bearer(token)

    r = api.post("/v1/admin/roles", json={"name": "support", "permissions": ["users.manage", "nope.x"]}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "UNKNOWN_PERMISSION"
    r = api.post("/v1/admin/roles", json={"name": "support", "permissions": ["users.manage", "audit.read"]}, headers=h)
    assert r.status_code == 201
    role = r.json()
    assert api.post("/v1/admin/roles", json={"name": "support"}, headers=h).status_code == 409

    weak = {"login": "agent", "password": "short", "role_ids": [role["id"]]}
    r = api.post("/v1/admin/admins", json=weak, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "WEAK_PASSWORD"
    r = api.post("/v1/admin/admins", json={**weak, "password": "Ag3nt-Passw0rd"}, headers=h)
    assert r.status_code == 201
    agent = r.json()
    assert agent["totp_uri"].startswith("otpauth://totp/")
    assert api.post("/v1/admin/admins", json={**weak, "password": "Ag3nt-Passw0rd"}, headers=h).status_code == 409

    agent_token = login(api, secret_of(agent["totp_uri"]), "agent", "Ag3nt-Passw0rd")
    assert agent_token["permissions"] == ["audit.read", "users.manage"]
    assert api.get("/v1/admin/admins", headers=bearer(agent_token["access"])).status_code == 403
    assert api.delete(f"/v1/admin/roles/{role['id']}", headers=h).status_code == 409  # ishlatilmoqda

    # Yagona superadmin o'zini o'chira olmaydi
    me = api.get("/v1/admin/auth/me", headers=h).json()
    r = api.patch(f"/v1/admin/admins/{me['id']}", json={"is_active": False}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "LAST_ADMIN_MANAGER"

    r = api.patch(f"/v1/admin/admins/{agent['admin']['id']}", json={"is_active": False, "role_ids": []}, headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False and r.json()["permissions"] == []
    r = api.post("/v1/admin/auth/login", json={"login": "agent", "password": "Ag3nt-Passw0rd"})
    assert r.status_code == 401
    assert api.delete(f"/v1/admin/roles/{role['id']}", headers=h).status_code == 204

    r = api.post(f"/v1/admin/admins/{me['id']}/reset-totp", headers=h)
    new_secret = secret_of(r.json()["totp_uri"])
    assert new_secret != secret
    login(api, new_secret)

    actions = [e["data"]["action"] for e in outbox(db, "audit.recorded")]
    for expected in ("role.create", "admin.create", "admin.update", "role.delete", "admin.reset_totp"):
        assert expected in actions
    for event in outbox(db, "audit.recorded"):
        assert_event_valid(event)


def test_change_password(api: TestClient) -> None:
    secret = bootstrap()
    h = bearer(login(api, secret)["access"])
    r = api.post("/v1/admin/auth/password", json={"current_password": "x", "new_password": "N3w-Passw0rd!"}, headers=h)
    assert r.status_code == 401
    body = {"current_password": PASSWORD, "new_password": "N3w-Passw0rd!"}
    assert api.post("/v1/admin/auth/password", json=body, headers=h).status_code == 204
    login(api, secret, password="N3w-Passw0rd!")


def test_settings_update_versioning_and_internal_etag(api: TestClient, db, admin_keys) -> None:
    admin_id = uuid.uuid4()
    h = bearer(mint_admin_token(admin_keys, admin_id=admin_id, permissions=["settings.manage"]))
    r = api.get("/v1/admin/settings", headers=h)
    assert r.status_code == 200 and r.headers["etag"] == '"v0"'
    values = r.json()["values"]
    assert values["commission"]["platform_bp"] == 1500 and values["schedule"]["hold_ttl_minutes"] == 10

    new_cancel = {**values["cancellation"], "full_refund_hours": 12}
    r = api.put("/v1/admin/settings", json={"expected_version": 5, "values": {"cancellation": new_cancel}}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["current_version"] == 0
    bad = {**new_cancel, "partial_refund_hours": 48}
    r = api.put("/v1/admin/settings", json={"expected_version": 0, "values": {"cancellation": bad}}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_SETTINGS"
    r = api.put("/v1/admin/settings", json={"expected_version": 0, "values": {"nope": {}}}, headers=h)
    assert r.status_code == 422
    r = api.put("/v1/admin/settings", json={"expected_version": 0, "values": {"cancellation": {"x": 1}}}, headers=h)
    assert r.status_code == 422  # noma'lum maydon

    r = api.put("/v1/admin/settings", json={"expected_version": 0, "values": {"cancellation": new_cancel}}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 1
    assert r.json()["values"]["cancellation"]["full_refund_hours"] == 12
    # O'zgarishsiz PUT — versiya oshmaydi, event yo'q
    r = api.put("/v1/admin/settings", json={"expected_version": 1, "values": {"cancellation": new_cancel}}, headers=h)
    assert r.json()["version"] == 1
    # Qisman o'zgartirish: boshqa maydonlar joriy qiymatini saqlaydi (standartga qaytmaydi)
    partial = {"expected_version": 1, "values": {"cancellation": {"partial_refund_percent": 30}}}
    r = api.put("/v1/admin/settings", json=partial, headers=h)
    assert r.json()["version"] == 2
    assert r.json()["values"]["cancellation"] == {**new_cancel, "partial_refund_percent": 30}

    changed = outbox(db, "admin.settings_changed")
    assert [e["data"]["version"] for e in changed] == [1, 2]
    assert changed[0]["data"] == {"keys": ["cancellation"], "version": 1, "changed_by": str(admin_id)}
    assert_event_valid(changed[0])
    audit = [e for e in outbox(db, "audit.recorded") if e["data"]["action"] == "settings.update"]
    assert audit[0]["data"]["before"]["cancellation"]["full_refund_hours"] == 24

    internal = {"X-Caller": "booking-service", "X-Internal-Token": "test-token"}
    r = api.get("/internal/v1/settings", headers=internal)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.headers["etag"] == '"v2"'
    assert "max-age=60" in r.headers["cache-control"]
    r = api.get("/internal/v1/settings", headers={**internal, "If-None-Match": '"v2"'})
    assert r.status_code == 304 and r.content == b""


def test_audit_consumer_and_query(api: TestClient, db, admin_keys, run_handler) -> None:
    actor = str(uuid.uuid4())

    def audit_data(action: str, object_id: str) -> dict[str, Any]:
        return {
            "actor_id": actor,
            "actor_type": "admin",
            "actor_role": "admin",
            "action": action,
            "object_type": "user",
            "object_id": object_id,
            "before": {"is_active": True},
            "after": {"is_active": False},
            "ip": "10.1.2.3",
            "request_id": "req-1",
        }

    first = run_handler("audit.recorded", audit_data("user.block", "u1"), producer="auth-service")
    # Takroriy yetkazish (bir xil event_id) — ikkinchi qator yo'q
    from medbron_common.eventbus import Envelope

    from app.services.audit import record_from_event

    async def again(session):
        return await record_from_event(session, Envelope.model_validate(first))

    assert db.run(again) is False
    for i in range(3):
        run_handler("audit.recorded", audit_data("file.view", f"f{i}"), producer="file-service")

    h = bearer(mint_admin_token(admin_keys, permissions=["audit.read"]))
    page = api.get("/v1/admin/audit", params={"limit": 2}, headers=h).json()
    assert [i["object_id"] for i in page["items"]] == ["f2", "f1"] and page["next_before_id"]
    page2 = api.get("/v1/admin/audit", params={"limit": 2, "before_id": page["next_before_id"]}, headers=h).json()
    assert [i["object_id"] for i in page2["items"]] == ["f0", "u1"]
    by_producer = api.get("/v1/admin/audit", params={"producer": "auth-service"}, headers=h).json()["items"]
    assert len(by_producer) == 1 and by_producer[0]["ip"] == "10.1.2.3" and by_producer[0]["action"] == "user.block"
    by_object = api.get("/v1/admin/audit", params={"object_type": "user", "object_id": "f1"}, headers=h).json()
    assert len(by_object["items"]) == 1
    assert api.get("/v1/admin/audit", headers=bearer(mint_admin_token(admin_keys, permissions=[]))).status_code == 403


def test_every_catalog_consume_has_handler() -> None:
    from medbron_common.eventbus import EventConsumer
    from medbron_common.events import CATALOG

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="admin-service", amqp_url="amqp://x/", session_factory=None, processed_model=ProcessedEvent
    )
    handlers.register(consumer)
    assert consumer.routing_keys == sorted(e for e, s in CATALOG.items() if "admin-service" in s.consumers)


def test_cli_create_and_reset(api: TestClient, db, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import io

    from app.cli import main as cli_main
    from app.core.database import get_engine, get_session_factory

    def main(argv: list[str]) -> int:
        # CLI — alohida jarayon; testda esa ilova engine'i boshqa event loop'da ochiq ulanishlarga ega.
        get_session_factory.cache_clear()
        get_engine.cache_clear()
        return cli_main(argv)

    monkeypatch.setattr("sys.stdin", io.StringIO("Cl1-Passw0rd-ok\n"))
    assert main(["create-admin", "--login", "cli@medbron.uz", "--password-stdin"]) == 0
    uri = capsys.readouterr().out.strip().splitlines()[-1]
    login(api, secret_of(uri), "cli@medbron.uz", "Cl1-Passw0rd-ok")

    monkeypatch.setattr("sys.stdin", io.StringIO("Cl1-Passw0rd-ok\n"))
    assert main(["create-admin", "--login", "cli@medbron.uz", "--password-stdin"]) == 1  # takror
    monkeypatch.setattr("sys.stdin", io.StringIO("weak\n"))
    assert main(["create-admin", "--login", "x2", "--password-stdin"]) == 1
    monkeypatch.setattr("sys.stdin", io.StringIO("Cl1-Passw0rd-ok\n"))
    assert main(["create-admin", "--login", "x3", "--role", "nope", "--password-stdin"]) == 1
    assert "XATO" in capsys.readouterr().err

    assert main(["reset-totp", "--login", "cli@medbron.uz"]) == 0
    new_uri = capsys.readouterr().out.strip().splitlines()[-1]
    assert secret_of(new_uri) != secret_of(uri)
    assert main(["reset-totp", "--login", "missing"]) == 1
