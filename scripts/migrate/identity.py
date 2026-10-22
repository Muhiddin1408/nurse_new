#!/usr/bin/env python3
"""2-bosqich: monolit → identity servislari, bir martalik ko'chirish (ARCHITECTURE 12).

O'SHA UUID'lar bilan (monolit, booking tarixi va yangi servislar bir-biriga mos qoladi). Idempotent:
`INSERT ... ON CONFLICT DO NOTHING` — qayta ishga tushirish xavfsiz (yangi qatorlar qo'shiladi,
mavjudlari tegilmaydi). Avval `--dry-run`: faqat manbadan o'qiydi va nima ko'chishini sanaydi.

    python scripts/migrate/identity.py --dry-run --source sqlite:///db.sqlite3
    python scripts/migrate/identity.py \\
        --source postgresql://monolith@.../medbron \\
        --auth postgresql://auth_service:...@127.0.0.1:5401/auth_service \\
        --user postgresql://user_service:...@127.0.0.1:5402/user_service \\
        --notification postgresql://notification_service:...@127.0.0.1:5405/notification_service \\
        --admin postgresql://admin_service:...@127.0.0.1:5403/admin_service \\
        --totp-key "$TOTP_ENCRYPTION_KEY"          # admin-service .env dagi bilan BIR XIL

Maqsad bazalarda migratsiyalar (alembic head) bajarilgan bo'lishi shart. Runbook: docs/runbooks/identity-cutover.md
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

USER_ROLES = {"client", "doctor", "clinic_admin"}
ACTIVE_BOOKING = {"pending_payment", "confirmed"}
REMINDER_KINDS = {
    "reminder_24h": ("booking_reminder_24h", "reminder"),
    "reminder_2h": ("booking_reminder_2h", "reminder"),
    "review_request": ("booking_review_request", "review_request"),
}


@dataclass
class Snapshot:
    users: list[dict[str, Any]] = field(default_factory=list)
    doctors: list[dict[str, Any]] = field(default_factory=list)
    memberships: list[dict[str, Any]] = field(default_factory=list)
    patients: list[dict[str, Any]] = field(default_factory=list)
    addresses: list[dict[str, Any]] = field(default_factory=list)
    favorites: list[dict[str, Any]] = field(default_factory=list)
    bookings: list[dict[str, Any]] = field(default_factory=list)
    preferences: list[dict[str, Any]] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)
    templates: list[dict[str, Any]] = field(default_factory=list)
    scheduled: list[dict[str, Any]] = field(default_factory=list)
    totp: list[dict[str, Any]] = field(default_factory=list)


def _u(value: Any) -> uuid.UUID | None:
    """SQLite UUID'ni 32 belgili hex sifatida, Postgres — uuid sifatida saqlaydi."""
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _dt(value: Any) -> datetime | None:
    """SQLite vaqtni naive string sifatida qaytaradi (Django USE_TZ — UTC)."""
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _engine(url: str) -> Engine:
    """`postgresql://` → psycopg (v3) drayveri; sqlite — o'zgarishsiz."""
    if url.startswith(("postgresql://", "postgres://")):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    return create_engine(url)


def _rows(conn: Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql)).mappings().all()]


def _deleted(phone: str | None) -> bool:
    return not phone or phone.startswith("deleted-")


def extract(engine: Engine) -> Snapshot:
    with engine.connect() as conn:
        snap = Snapshot(
            users=_rows(
                conn,
                "SELECT id, phone, full_name, role, preferred_language, is_phone_verified, is_active, is_superuser,"
                " password, last_login, created_at, updated_at FROM account_user",
            ),
            doctors=_rows(
                conn,
                "SELECT d.id, d.user_id, d.status, d.telegram_chat_id, d.moderated_at, d.updated_at, u.full_name"
                " FROM catalog_doctor d JOIN account_user u ON u.id = d.user_id",
            ),
            memberships=_rows(conn, "SELECT user_id, clinic_id, is_active, updated_at FROM catalog_clinicmembership"),
            patients=_rows(
                conn,
                "SELECT id, owner_id, full_name, relation, birth_date, gender, weight_kg, is_deleted, created_at,"
                " updated_at FROM account_patient",
            ),
            addresses=_rows(
                conn,
                "SELECT id, user_id, label, city, district, street, entrance, floor, apartment, comment, latitude,"
                " longitude, is_default, is_deleted, created_at, updated_at FROM account_address",
            ),
            favorites=_rows(conn, "SELECT user_id, doctor_id, created_at FROM account_favoritedoctor"),
            bookings=_rows(
                conn,
                "SELECT b.id, b.number, b.client_id, b.patient_id, b.doctor_id, b.status, b.updated_at,"
                " s.start_at, s.clinic_id FROM booking_booking b JOIN schedule_timeslot s ON s.id = b.slot_id",
            ),
            preferences=_rows(
                conn,
                "SELECT user_id, push_enabled, telegram_enabled, sms_enabled, marketing_opt_in, telegram_chat_id"
                " FROM notifications_notificationpreference",
            ),
            devices=_rows(
                conn, "SELECT user_id, token, platform, is_active, last_seen_at, created_at FROM notifications_pushdevice"
            ),
            templates=_rows(conn, "SELECT key, language, text, is_active FROM notifications_notificationtemplate"),
            scheduled=_rows(
                conn,
                "SELECT id, booking_id, kind, params, send_at, status FROM notifications_schedulednotification"
                " WHERE status = 'pending'",
            ),
            totp=_rows(conn, "SELECT user_id, secret, last_step FROM account_admintotpdevice"),
        )
    return snap


def _put(conn: Connection, table_name: str, rows: Iterable[dict[str, Any]], conflict: list[str]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    table = Table(table_name, MetaData(), autoload_with=conn)
    inserted = 0
    for start in range(0, len(rows), 500):
        stmt = (
            insert(table)
            .values(rows[start : start + 500])
            .on_conflict_do_nothing(index_elements=conflict)
            .returning(table.c[conflict[0]])  # rowcount ko'p qatorli INSERT'da ishonchsiz (-1)
        )
        inserted += len(conn.execute(stmt).all())
    return inserted


def _roles(snap: Snapshot) -> dict[uuid.UUID, list[str]]:
    roles: dict[uuid.UUID, list[str]] = {_u(u["id"]): ["client"] for u in snap.users}  # type: ignore[misc]
    for d in snap.doctors:
        if d["status"] in ("approved", "suspended"):
            roles.setdefault(_u(d["user_id"]), ["client"]).append("doctor")  # type: ignore[arg-type]
    for m in snap.memberships:
        uid = _u(m["user_id"])
        if m["is_active"] and "clinic_admin" not in roles.get(uid, []):  # type: ignore[arg-type]
            roles.setdefault(uid, ["client"]).append("clinic_admin")  # type: ignore[arg-type]
    return roles


def plan_auth(snap: Snapshot) -> dict[str, list[dict[str, Any]]]:
    users = []
    for u in snap.users:
        deleted = _deleted(u["phone"])
        users.append(
            {
                "id": _u(u["id"]),
                "phone": None if deleted else u["phone"],
                "is_active": bool(u["is_active"]) and not deleted,
                "is_phone_verified": bool(u["is_phone_verified"]),
                "deactivated_reason": "deletion_requested" if deleted else (None if u["is_active"] else "blocked_by_admin"),
                "preferred_role": u["role"] if u["role"] in USER_ROLES else "client",
                "last_login_at": _dt(u["last_login"]),
                "created_at": _dt(u["created_at"]),
                "updated_at": _dt(u["updated_at"]),
            }
        )
    doctor_links = [
        {
            "user_id": _u(d["user_id"]),
            "doctor_id": _u(d["id"]),
            "status": d["status"],
            "changed_at": _dt(d["moderated_at"] or d["updated_at"]),
        }
        for d in snap.doctors
        if d["status"] in ("approved", "suspended")
    ]
    memberships = [
        {
            "user_id": _u(m["user_id"]),
            "clinic_id": _u(m["clinic_id"]),
            "is_active": bool(m["is_active"]),
            "changed_at": _dt(m["updated_at"]),
        }
        for m in snap.memberships
    ]
    return {"users": users, "doctor_links": doctor_links, "clinic_memberships": memberships}


def plan_user(snap: Snapshot) -> dict[str, list[dict[str, Any]]]:
    profiles = [
        {
            "user_id": _u(u["id"]),
            "full_name": "" if _deleted(u["phone"]) else (u["full_name"] or ""),
            "preferred_language": u["preferred_language"] or "uz",
            "deleted_at": _dt(u["updated_at"]) if _deleted(u["phone"]) else None,
            "created_at": _dt(u["created_at"]),
            "updated_at": _dt(u["updated_at"]),
        }
        for u in snap.users
    ]
    patients = [
        {
            "id": _u(p["id"]),
            "user_id": _u(p["owner_id"]),
            "full_name": p["full_name"],
            "relation": p["relation"] or "",
            "birth_date": p["birth_date"],
            "gender": p["gender"],
            "weight_kg": p["weight_kg"],
            "is_deleted": bool(p["is_deleted"]),
            "created_at": _dt(p["created_at"]),
            "updated_at": _dt(p["updated_at"]),
        }
        for p in snap.patients
    ]
    # Bitta standart manzil (yangi servisda qisman UNIQUE indeks): eng yangisi qoladi
    seen_default: set[uuid.UUID] = set()
    addresses = []
    for a in sorted(snap.addresses, key=lambda r: str(r["updated_at"]), reverse=True):
        uid = _u(a["user_id"])
        is_default = bool(a["is_default"]) and not a["is_deleted"] and uid not in seen_default
        if is_default:
            seen_default.add(uid)  # type: ignore[arg-type]
        addresses.append(
            {
                "id": _u(a["id"]),
                "user_id": uid,
                **{k: a[k] or "" for k in ("label", "city", "district", "street", "entrance", "floor", "apartment",
                                          "comment")},
                "latitude": a["latitude"],
                "longitude": a["longitude"],
                "is_default": is_default,
                "is_deleted": bool(a["is_deleted"]),
                "created_at": _dt(a["created_at"]),
                "updated_at": _dt(a["updated_at"]),
            }
        )  # fmt: skip
    favorites = [
        {"user_id": _u(f["user_id"]), "doctor_id": _u(f["doctor_id"]), "created_at": _dt(f["created_at"])}
        for f in snap.favorites
    ]
    bookings = [
        {
            "booking_id": _u(b["id"]),
            "user_id": _u(b["client_id"]),
            "patient_id": _u(b["patient_id"]),
            "status": b["status"],
            "is_active": b["status"] in ACTIVE_BOOKING,
            "changed_at": _dt(b["updated_at"]),
        }
        for b in snap.bookings
    ]
    return {"profiles": profiles, "patients": patients, "addresses": addresses, "favorites": favorites,
            "booking_states": bookings}  # fmt: skip


def plan_notification(snap: Snapshot, now: datetime) -> dict[str, list[dict[str, Any]]]:
    roles = _roles(snap)
    prefs = {_u(p["user_id"]): p for p in snap.preferences}
    doctor_chat = {_u(d["user_id"]): d["telegram_chat_id"] for d in snap.doctors if d["telegram_chat_id"]}
    used_chats: set[int] = set()
    contacts = []
    for u in snap.users:
        uid = _u(u["id"])
        pref = prefs.get(uid, {})
        chat = pref.get("telegram_chat_id") or doctor_chat.get(uid)  # type: ignore[arg-type]
        if chat in used_chats:  # bitta chat — bitta akkaunt (UNIQUE)
            chat = None
        if chat:
            used_chats.add(chat)
        deleted = _deleted(u["phone"])
        contacts.append(
            {
                "user_id": uid,
                "phone": None if deleted else u["phone"],
                "full_name": "" if deleted else (u["full_name"] or ""),
                "language": u["preferred_language"] or "uz",
                "roles": roles.get(uid, ["client"]),  # type: ignore[arg-type]
                "is_active": bool(u["is_active"]) and not deleted,
                "telegram_chat_id": None if deleted else chat,
                "push_enabled": bool(pref.get("push_enabled", True)),
                "telegram_enabled": bool(pref.get("telegram_enabled", True)),
                "sms_enabled": bool(pref.get("sms_enabled", True)),
                "marketing_opt_in": bool(pref.get("marketing_opt_in", False)) and not deleted,
            }
        )
    doctor_contacts = [
        {"doctor_id": _u(d["id"]), "user_id": _u(d["user_id"]), "full_name": d["full_name"] or "",
         "changed_at": _dt(d["updated_at"])}
        for d in snap.doctors
        if d["status"] in ("approved", "suspended")
    ]  # fmt: skip
    devices = [
        {"id": uuid.uuid4(), "user_id": _u(d["user_id"]), "token": d["token"], "platform": d["platform"],
         "is_active": bool(d["is_active"]), "last_seen_at": _dt(d["last_seen_at"]), "created_at": _dt(d["created_at"])}
        for d in snap.devices
    ]  # fmt: skip
    templates = [{"key": t["key"], "language": t["language"], "text": t["text"], "is_active": bool(t["is_active"])}
                 for t in snap.templates]  # fmt: skip
    horizon = now - timedelta(days=1)
    projection = []
    by_booking = {}
    for b in snap.bookings:
        start = _dt(b["start_at"])
        by_booking[_u(b["id"])] = b
        if start is not None and start >= horizon:
            projection.append(
                {"booking_id": _u(b["id"]), "number": b["number"], "client_id": _u(b["client_id"]),
                 "doctor_id": _u(b["doctor_id"]), "place": "clinic" if b["clinic_id"] else "home", "start_at": start,
                 "status": b["status"], "changed_at": _dt(b["updated_at"])}
            )  # fmt: skip
    notifications = []
    for s in snap.scheduled:
        mapped = REMINDER_KINDS.get(s["kind"])
        booking = by_booking.get(_u(s["booking_id"]))
        if mapped is None or booking is None:
            continue
        template, kind = mapped
        params = s["params"] if isinstance(s["params"], dict) else json.loads(s["params"] or "{}")
        notifications.append(
            {"id": uuid.uuid4(), "audience": "user", "user_id": _u(booking["client_id"]), "template": template,
             "params": params, "booking_id": _u(booking["id"]), "kind": kind, "send_at": _dt(s["send_at"]),
             "status": "pending", "dedupe_key": f"{template}:{_u(booking['id'])}"}
        )  # fmt: skip
    return {"contacts": contacts, "doctor_contacts": doctor_contacts, "push_devices": devices, "templates": templates,
            "booking_projection": projection, "notifications": notifications}  # fmt: skip


def plan_admin(snap: Snapshot, totp_key: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Monolitdagi platform_admin/superuser → admin-service (login = telefon). Django pbkdf2 xeshi saqlanadi
    va birinchi kirishda argon2'ga almashtiriladi. TOTP'si yo'q admin — yangi sir (qayta ro'yxatdan o'tish)."""
    from cryptography.fernet import Fernet

    fernet = Fernet(totp_key.encode()) if totp_key else None
    devices = {_u(t["user_id"]): t for t in snap.totp}
    admins, needs_enrollment = [], []
    for u in snap.users:
        if not (u["role"] == "platform_admin" or u["is_superuser"]) or _deleted(u["phone"]):
            continue
        if not u["password"] or u["password"].startswith("!"):
            needs_enrollment.append(f"{u['phone']}: parol yo'q — `app.cli` bilan yarating")
            continue
        device = devices.get(_u(u["id"]))
        secret = device["secret"] if device else base64.b32encode(os.urandom(20)).decode()
        if device is None:
            needs_enrollment.append(f"{u['phone']}: TOTP yo'q edi — `python -m app.cli reset-totp --login {u['phone']}`")
        admins.append(
            {
                "id": _u(u["id"]),
                "login": u["phone"].lower(),
                "full_name": u["full_name"] or "",
                "password_hash": u["password"],
                "totp_secret_enc": fernet.encrypt(secret.encode()).decode() if fernet else "",
                "totp_last_step": device["last_step"] if device else None,
                "is_active": bool(u["is_active"]),
                "created_at": _dt(u["created_at"]),
            }
        )
    return admins, needs_enrollment


def _load(engine: Engine, plan: dict[str, list[dict[str, Any]]], conflicts: dict[str, list[str]]) -> dict[str, int]:
    out = {}
    with engine.begin() as conn:
        for table, rows in plan.items():
            out[table] = _put(conn, table, rows, conflicts[table])
    return out


CONFLICTS = {
    "users": ["id"], "doctor_links": ["user_id"], "clinic_memberships": ["user_id", "clinic_id"],
    "profiles": ["user_id"], "patients": ["id"], "addresses": ["id"], "favorites": ["user_id", "doctor_id"],
    "booking_states": ["booking_id"], "contacts": ["user_id"], "doctor_contacts": ["doctor_id"],
    "push_devices": ["token"], "templates": ["key", "language"], "booking_projection": ["booking_id"],
    "notifications": ["dedupe_key"],
}  # fmt: skip


def run(args: argparse.Namespace) -> int:
    source = _engine(args.source)
    snap = extract(source)
    now = datetime.now(UTC)
    auth_plan, user_plan, notif_plan = plan_auth(snap), plan_user(snap), plan_notification(snap, now)
    admins, enrollment = plan_admin(snap, args.totp_key)
    report: dict[str, Any] = {"source": {k: len(v) for k, v in vars(snap).items()}}
    if args.dry_run:
        report["plan"] = {
            "auth": {k: len(v) for k, v in auth_plan.items()},
            "user": {k: len(v) for k, v in user_plan.items()},
            "notification": {k: len(v) for k, v in notif_plan.items()},
            "admin": {"admins": len(admins)},
        }
    else:
        missing = [n for n in ("auth", "user", "notification", "admin") if not getattr(args, n)]
        if missing:
            print(f"XATO: maqsad bazalar ko'rsatilmagan: {', '.join(missing)}", file=sys.stderr)
            return 2
        if admins and not args.totp_key:
            print("XATO: adminlar bor — --totp-key (admin-service TOTP_ENCRYPTION_KEY) majburiy", file=sys.stderr)
            return 2
        report["inserted"] = {
            "auth": _load(_engine(args.auth), auth_plan, CONFLICTS),
            "user": _load(_engine(args.user), user_plan, CONFLICTS),
            "notification": _load(_engine(args.notification), notif_plan, CONFLICTS),
        }
        with _engine(args.admin).begin() as conn:
            inserted = _put(conn, "admins", admins, ["id"])
            role_id = conn.execute(text("SELECT id FROM roles WHERE name = 'superadmin'")).scalar_one()
            _put(conn, "admin_roles", [{"admin_id": a["id"], "role_id": role_id} for a in admins], ["admin_id", "role_id"])
        report["inserted"]["admin"] = {"admins": inserted}
    report["admin_followup"] = enrollment
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.environ.get("MONOLITH_DATABASE_URL"), required=False)
    for name in ("auth", "user", "notification", "admin"):
        parser.add_argument(f"--{name}", default=os.environ.get(f"{name.upper()}_DATABASE_URL"))
    parser.add_argument("--totp-key", default=os.environ.get("TOTP_ENCRYPTION_KEY"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.source:
        parser.error("--source (yoki MONOLITH_DATABASE_URL) majburiy")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
