"""scripts/migrate/identity.py — xaritalash qoidalari (manba: monolit qatorlari, maqsad: servis jadvallari).

    pip install -r scripts/migrate/requirements.txt pytest && pytest scripts/migrate -q
Haqiqiy bazalar bilan to'liq ishga tushirish — docs/runbooks/identity-cutover.md (dry-run + delta).
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent))
import identity  # noqa: E402

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def user(**kw):
    base = {"id": uuid.uuid4().hex, "phone": "+998901112233", "full_name": "Ali", "role": "client",
            "preferred_language": "uz", "is_phone_verified": 1, "is_active": 1, "is_superuser": 0,
            "password": "!unusable", "last_login": None, "created_at": "2026-01-01 10:00:00",
            "updated_at": "2026-01-02 10:00:00"}  # fmt: skip
    return {**base, **kw}


def test_users_roles_and_deleted_accounts() -> None:
    alive, deleted, blocked = user(role="doctor"), user(phone="deleted-abc123", is_active=0), user(phone="+998935556677", is_active=0)
    doctor = {"id": uuid.uuid4().hex, "user_id": alive["id"], "status": "approved", "telegram_chat_id": 555,
              "moderated_at": None, "updated_at": "2026-02-01 00:00:00", "full_name": "Ali"}  # fmt: skip
    clinic = uuid.uuid4().hex
    snap = identity.Snapshot(users=[alive, deleted, blocked], doctors=[doctor],
                             memberships=[{"user_id": alive["id"], "clinic_id": clinic, "is_active": 1,
                                           "updated_at": "2026-02-01 00:00:00"}])  # fmt: skip
    auth = identity.plan_auth(snap)
    by_id = {str(u["id"]): u for u in auth["users"]}
    a = by_id[str(uuid.UUID(alive["id"]))]
    assert a["phone"] == "+998901112233" and a["preferred_role"] == "doctor" and a["is_active"]
    assert a["created_at"].tzinfo is not None
    d = by_id[str(uuid.UUID(deleted["id"]))]
    assert d["phone"] is None and d["deactivated_reason"] == "deletion_requested" and not d["is_active"]
    assert by_id[str(uuid.UUID(blocked["id"]))]["deactivated_reason"] == "blocked_by_admin"
    assert auth["doctor_links"][0]["doctor_id"] == uuid.UUID(doctor["id"])
    assert auth["clinic_memberships"][0]["clinic_id"] == uuid.UUID(clinic)

    notif = identity.plan_notification(snap, NOW)
    contact = next(c for c in notif["contacts"] if c["user_id"] == uuid.UUID(alive["id"]))
    assert contact["roles"] == ["client", "doctor", "clinic_admin"] and contact["telegram_chat_id"] == 555
    assert notif["doctor_contacts"][0]["user_id"] == uuid.UUID(alive["id"])
    gone = next(c for c in notif["contacts"] if c["user_id"] == uuid.UUID(deleted["id"]))
    assert gone["phone"] is None and gone["full_name"] == "" and not gone["marketing_opt_in"]


def test_single_default_address_and_profiles() -> None:
    u = user()
    def address(updated: str) -> dict:
        return {"id": uuid.uuid4().hex, "user_id": u["id"], "label": "", "city": "Toshkent", "district": None,
                "street": "x", "entrance": "", "floor": "", "apartment": "", "comment": "", "latitude": 41.3,
                "longitude": 69.2, "is_default": 1, "is_deleted": 0, "created_at": updated, "updated_at": updated}  # fmt: skip
    old, new = address("2026-01-01 00:00:00"), address("2026-03-01 00:00:00")
    plan = identity.plan_user(identity.Snapshot(users=[u], addresses=[old, new]))
    defaults = {str(a["id"]): a["is_default"] for a in plan["addresses"]}
    assert defaults == {str(uuid.UUID(new["id"])): True, str(uuid.UUID(old["id"])): False}
    assert plan["addresses"][0]["district"] == ""
    assert plan["profiles"][0]["full_name"] == "Ali"


def test_bookings_projection_and_scheduled_reminders() -> None:
    u = user()
    future = {"id": uuid.uuid4().hex, "number": "MB-1", "client_id": u["id"], "patient_id": uuid.uuid4().hex,
              "doctor_id": uuid.uuid4().hex, "status": "confirmed", "updated_at": "2026-10-20 00:00:00",
              "start_at": (NOW + timedelta(days=2)).isoformat(), "clinic_id": None}  # fmt: skip
    past = {**future, "id": uuid.uuid4().hex, "status": "completed", "start_at": (NOW - timedelta(days=10)).isoformat()}
    scheduled = {"id": uuid.uuid4().hex, "booking_id": future["id"], "kind": "reminder_24h",
                 "params": '{"when": "23.10 10:00", "place": "Uy chaqiruvi"}', "send_at": "2026-10-22 05:00:00",
                 "status": "pending"}  # fmt: skip
    snap = identity.Snapshot(users=[u], bookings=[future, past], scheduled=[scheduled])
    states = identity.plan_user(snap)["booking_states"]
    assert {s["status"]: s["is_active"] for s in states} == {"confirmed": True, "completed": False}
    notif = identity.plan_notification(snap, NOW)
    assert [b["number"] for b in notif["booking_projection"]] == ["MB-1"]  # o'tmishdagi kirmaydi
    assert notif["booking_projection"][0]["place"] == "home"
    reminder = notif["notifications"][0]
    assert reminder["template"] == "booking_reminder_24h" and reminder["kind"] == "reminder"
    assert reminder["dedupe_key"] == f"booking_reminder_24h:{uuid.UUID(future['id'])}"
    assert reminder["params"]["place"] == "Uy chaqiruvi"


def test_platform_admins() -> None:
    key = Fernet.generate_key().decode()
    admin = user(role="platform_admin", phone="+998712000000", password="pbkdf2_sha256$870000$s$h")
    no_totp = user(is_superuser=1, phone="+998712000001", password="pbkdf2_sha256$870000$s$h2")
    no_password = user(role="platform_admin", phone="+998712000002")
    snap = identity.Snapshot(users=[admin, no_totp, no_password, user()],
                             totp=[{"user_id": admin["id"], "secret": "JBSWY3DPEHPK3PXP", "last_step": 99}])  # fmt: skip
    admins, followup = identity.plan_admin(snap, key)
    assert [a["login"] for a in admins] == ["+998712000000", "+998712000001"]
    assert Fernet(key.encode()).decrypt(admins[0]["totp_secret_enc"].encode()) == b"JBSWY3DPEHPK3PXP"
    assert admins[0]["password_hash"].startswith("pbkdf2_sha256$") and admins[0]["totp_last_step"] == 99
    assert len(followup) == 2 and any("reset-totp" in f for f in followup) and any("parol yo'q" in f for f in followup)


def test_dry_run_against_sqlite(tmp_path: Path, capsys) -> None:
    """Haqiqiy monolit sxemasi (db.sqlite3) bo'lsa — dry-run o'qiy olishini tekshiradi."""
    db = Path(__file__).resolve().parents[2] / "db.sqlite3"
    if not db.is_file():
        import pytest

        pytest.skip("db.sqlite3 yo'q")
    assert identity.main(["--dry-run", "--source", f"sqlite:///{db}"]) == 0
    out = capsys.readouterr().out
    assert '"plan"' in out and '"auth"' in out
