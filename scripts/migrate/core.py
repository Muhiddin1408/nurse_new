#!/usr/bin/env python3
"""3-bosqich: monolit → catalog, schedule, pricing, booking — bir martalik ko'chirish (ADR-0011).

O'SHA UUID'lar bilan (2-bosqich identity ko'chirishi va bronlar tarixi mos qoladi). Idempotent:
`INSERT ... ON CONFLICT DO NOTHING` — qayta ishga tushirish xavfsiz. Bog'liq jadvallar (bron qatorlari,
narx tarixi) faqat SHU ishga tushishda yangi qo'shilgan egasi uchun yoziladi — takror yozilmaydi.
Pul: so'm (Decimal) → tiyin ×100, ROUND_HALF_UP; stavka (0.1500) → bp (1500).
Mos kelmaydigan qator jim tashlanmaydi — hisobotdagi `skipped` da sabab bilan sanaladi.

    python scripts/migrate/core.py --dry-run --source sqlite:///db.sqlite3
    python scripts/migrate/core.py --source postgresql://monolith@.../medbron \\
        --catalog postgresql://catalog_service:...@127.0.0.1:5406/catalog_service \\
        --schedule postgresql://schedule_service:...@127.0.0.1:5407/schedule_service \\
        --pricing postgresql://pricing_service:...@127.0.0.1:5408/pricing_service \\
        --booking postgresql://booking_service:...@127.0.0.1:5409/booking_service

Har maqsad ixtiyoriy (berilmagani o'tkazib yuboriladi). Maqsad bazalarda migratsiyalar bajarilgan bo'lishi,
schedule-scheduler esa hali ishga TUSHMAGAN bo'lishi shart (generatsiya slotlari bilan to'qnashmasin).
Runbook: docs/runbooks/core-cutover.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

LIVE = ("pending_payment", "confirmed")
VISITED = ("completed", "no_show")
CLINIC_STATUS = {"draft": "draft", "pending": "draft", "active": "active", "suspended": "suspended"}
DOCTOR_STATUS = {"draft", "pending", "approved", "rejected", "suspended"}
AFFILIATION_STATUS = {"invited", "active", "paused", "declined", "ended"}
BOOKING_STATUS = {"pending_payment", "confirmed", "completed", "cancelled", "expired", "no_show"}
PAID_STATUSES = ("succeeded", "refunded", "partially_refunded")
# Uy chaqiruvi slotida bufer yozilmagan bo'lsa — yo'l bloki egasini shu oraliqda qidiramiz
DEFAULT_BUFFER_MINUTES = 60


@dataclass
class Snapshot:
    specializations: list[dict[str, Any]] = field(default_factory=list)
    clinics: list[dict[str, Any]] = field(default_factory=list)
    rooms: list[dict[str, Any]] = field(default_factory=list)
    doctors: list[dict[str, Any]] = field(default_factory=list)
    doctor_specializations: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    memberships: list[dict[str, Any]] = field(default_factory=list)
    affiliations: list[dict[str, Any]] = field(default_factory=list)
    services: list[dict[str, Any]] = field(default_factory=list)
    price_history: list[dict[str, Any]] = field(default_factory=list)
    price_rules: list[dict[str, Any]] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    closures: list[dict[str, Any]] = field(default_factory=list)
    time_offs: list[dict[str, Any]] = field(default_factory=list)
    slots: list[dict[str, Any]] = field(default_factory=list)
    bookings: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    waitlist: list[dict[str, Any]] = field(default_factory=list)
    promos: list[dict[str, Any]] = field(default_factory=list)
    redemptions: list[dict[str, Any]] = field(default_factory=list)
    enrollments: list[dict[str, Any]] = field(default_factory=list)
    package_uses: list[dict[str, Any]] = field(default_factory=list)
    patients: list[dict[str, Any]] = field(default_factory=list)
    addresses: list[dict[str, Any]] = field(default_factory=list)
    payments: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)


Plan = dict[str, list[dict[str, Any]]]


# --- turlar (SQLite: UUID — 32 hex, vaqt/sana — string, JSON — string, Decimal — int/float/str) ----------


def _u(value: Any) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _dt(value: Any) -> datetime | None:
    """Django USE_TZ — UTC; SQLite naive string qaytaradi."""
    if value is None or value == "":
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _d(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _t(value: Any) -> time | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, time) else time.fromisoformat(str(value))


def _json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value) if isinstance(value, str | bytes) else value


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def tiyin(value: Any) -> int | None:
    """So'm → tiyin (ADR-0007): ×100, ROUND_HALF_UP."""
    amount = _dec(value)
    return None if amount is None else int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def bp(rate: Any) -> int:
    """Stavka (0.1500) → basis point (1500)."""
    value = _dec(rate) or Decimal(0)
    return int((value * 10_000).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _engine(url: str) -> Engine:
    if url.startswith(("postgresql://", "postgres://")):
        url = "postgresql+psycopg://" + url.split("://", 1)[1]
    return create_engine(url)


def _rows(conn: Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql)).mappings().all()]


# --- o'qish -----------------------------------------------------------------------------------------------

QUERIES = {
    "specializations": "SELECT id, name, name_ru, slug, is_pediatric, accepts_children, min_patient_age,"
    " max_patient_age, allowed_gender, created_at FROM catalog_specialization",
    "clinics": "SELECT id, name, description, phone, city, street, latitude, longitude, status, rating,"
    " reviews_count, working_hours, photo_url, created_at, updated_at FROM catalog_clinic",
    "rooms": "SELECT id, clinic_id, name, floor, equipment, is_active, created_at FROM catalog_room",
    "doctors": "SELECT d.id, d.user_id, u.full_name, d.bio, d.experience_years, d.education, d.languages,"
    " d.license_number, d.license_expires_at, d.license_reminder_sent_at, d.status, d.submitted_at,"
    " d.moderated_at, d.moderated_by_id, d.status_reason, d.clinic_payment_mode, d.follow_up_discount_percent,"
    " d.accepts_home_visits, d.home_visit_radius_km, d.home_base_latitude, d.home_base_longitude, d.rating,"
    " d.reviews_count, d.created_at, d.updated_at FROM catalog_doctor d JOIN account_user u ON u.id = d.user_id",
    "doctor_specializations": "SELECT doctor_id, specialization_id FROM catalog_doctor_specializations",
    "documents": "SELECT id, doctor_id, kind FROM catalog_doctordocument",
    "memberships": "SELECT id, user_id, clinic_id, is_active, created_at, updated_at FROM catalog_clinicmembership",
    "affiliations": "SELECT id, doctor_id, clinic_id, position, status, invited_by_id, responded_at, created_at,"
    " updated_at FROM catalog_doctoraffiliation",
    "services": "SELECT id, clinic_id, doctor_id, specialization_id, name, name_ru, description, place,"
    " duration_minutes, price, is_active, mxik_code, package_code, vat_percent, created_at, updated_at"
    " FROM catalog_service",
    "price_history": "SELECT service_id, old_price, new_price, changed_by_id, is_large_change, created_at"
    " FROM catalog_servicepricehistory",
    "price_rules": "SELECT id, doctor_id, name, weekdays, start_time, end_time, percent, priority, is_active,"
    " created_at, updated_at FROM catalog_pricerule",
    "packages": "SELECT id, doctor_id, service_id, name, sessions, discount_percent, validity_days, is_active,"
    " created_at FROM catalog_servicepackage",
    "rules": "SELECT id, doctor_id, clinic_id, room_id, weekday, start_time, end_time, slot_minutes, valid_from,"
    " valid_to, created_at, updated_at FROM schedule_workingrule",
    "exceptions": "SELECT id, doctor_id, date, reason, created_at FROM schedule_scheduleexception",
    "closures": "SELECT id, clinic_id, date, reason, created_at FROM schedule_clinicclosure",
    "time_offs": "SELECT id, doctor_id, start_at, end_at, kind, reason, created_at FROM schedule_timeoff",
    "slots": "SELECT id, doctor_id, clinic_id, room_id, start_at, end_at, status, hold_expires_at, block_reason,"
    " time_off_id, block_note, travel_buffer_minutes, created_at, updated_at FROM schedule_timeslot",
    "bookings": "SELECT id, number, client_id, patient_id, doctor_id, slot_id, address_id, payment_mode, status,"
    " total_price, original_price, discount_amount, discount_kind, promo_code, price_adjust_percent,"
    " discount_borne_by, prepay_amount, commission_rate, provider_fee_rate, platform_fee, provider_fee,"
    " doctor_payout, client_comment, cancelled_reason, started_at, completed_at, completed_by, no_show_by, note,"
    " rescheduled_count, created_at, updated_at FROM booking_booking",
    "items": "SELECT booking_id, service_id, service_name, price, duration_minutes, mxik_code, package_code,"
    " vat_percent FROM booking_bookingitem",
    "waitlist": "SELECT id, client_id, doctor_id, date_from, date_to, place, status, notified_at, notified_count,"
    " created_at FROM booking_waitlistentry",
    "promos": "SELECT id, code, kind, value, max_discount, min_amount, valid_from, valid_until, max_uses,"
    " max_uses_per_user, first_booking_only, doctor_id, borne_by, is_active, created_at, updated_at"
    " FROM booking_promocode",
    "redemptions": "SELECT id, promo_id, booking_id, client_id, amount, is_active, created_at, updated_at"
    " FROM booking_promoredemption",
    "enrollments": "SELECT id, package_id, client_id, doctor_id, service_id, sessions_total, discount_percent,"
    " expires_at, is_active, created_at FROM booking_packageenrollment",
    "package_uses": "SELECT id, enrollment_id, booking_id, amount, is_active, created_at, updated_at"
    " FROM booking_packageuse",
    "patients": "SELECT id, full_name, relation, birth_date, gender, weight_kg FROM account_patient",
    "addresses": "SELECT id, label, city, district, street, entrance, floor, apartment, comment, latitude,"
    " longitude FROM account_address",
    "payments": "SELECT id, booking_id, amount, status, updated_at FROM payment_payment",
    "refunds": "SELECT booking_id, amount, status FROM payment_refund",
}


def extract(engine: Engine) -> Snapshot:
    with engine.connect() as conn:
        return Snapshot(**{name: _rows(conn, sql) for name, sql in QUERIES.items()})


# --- catalog ----------------------------------------------------------------------------------------------


def plan_catalog(snap: Snapshot, skipped: Counter[str]) -> Plan:
    specializations = [
        {
            "id": _u(s["id"]),
            "name": s["name"],
            "name_ru": s["name_ru"] or "",
            "slug": s["slug"],
            "is_pediatric": bool(s["is_pediatric"]),
            "accepts_children": bool(s["accepts_children"]),
            "min_patient_age": s["min_patient_age"],
            "max_patient_age": s["max_patient_age"],
            "allowed_gender": s["allowed_gender"] or "",
            "created_at": _dt(s["created_at"]),
        }
        for s in snap.specializations
    ]
    clinics = []
    for c in snap.clinics:
        status = CLINIC_STATUS.get(c["status"])
        if status is None:
            skipped[f"catalog.clinics: noma'lum holat '{c['status']}'"] += 1
            continue
        if c["status"] == "pending":
            skipped["catalog.clinics: pending → draft (servisda moderatsiya holati yo'q; qatorlar ko'chdi)"] += 1
        reviews = int(c["reviews_count"] or 0)
        clinics.append(
            {
                "id": _u(c["id"]),
                "name": c["name"],
                "description": c["description"] or "",
                "phone": c["phone"] or "",
                "city": c["city"],
                "street": c["street"],
                "latitude": _dec(c["latitude"]),
                "longitude": _dec(c["longitude"]),
                "status": status,
                "working_hours": _json(c["working_hours"], {}),
                "photo_url": c["photo_url"] or None,
                "rating": _dec(c["rating"]) if reviews else None,
                "reviews_count": reviews,
                "created_at": _dt(c["created_at"]),
                "updated_at": _dt(c["updated_at"]),
            }
        )
    rooms = [
        {
            "id": _u(r["id"]),
            "clinic_id": _u(r["clinic_id"]),
            "name": r["name"],
            "floor": r["floor"] or "",
            "equipment": [str(e)[:50] for e in _json(r["equipment"], [])][:30],
            "is_active": bool(r["is_active"]),
            "created_at": _dt(r["created_at"]),
        }
        for r in snap.rooms
    ]
    doctors = []
    for d in snap.doctors:
        if d["status"] not in DOCTOR_STATUS:
            skipped[f"catalog.doctors: noma'lum holat '{d['status']}'"] += 1
            continue
        reviews = int(d["reviews_count"] or 0)
        doctors.append(
            {
                "id": _u(d["id"]),
                "user_id": _u(d["user_id"]),
                "full_name": d["full_name"] or "",
                "bio": d["bio"] or "",
                "education": d["education"] or "",
                "experience_years": int(d["experience_years"] or 0),
                "languages": [str(lang)[:5] for lang in _json(d["languages"], [])],
                "license_number": d["license_number"] or "",
                "license_expires_at": _d(d["license_expires_at"]),
                "license_reminder_sent_at": _dt(d["license_reminder_sent_at"]),
                "status": d["status"],
                "status_reason": d["status_reason"] or "",
                "submitted_at": _dt(d["submitted_at"]),
                "moderated_at": _dt(d["moderated_at"]),
                "moderated_by": _u(d["moderated_by_id"]),
                "clinic_payment_mode": d["clinic_payment_mode"] or "prepaid",
                "follow_up_discount_percent": int(d["follow_up_discount_percent"] or 0),
                "accepts_home_visits": bool(d["accepts_home_visits"]),
                "home_visit_radius_km": int(d["home_visit_radius_km"] or 10),
                "home_base_latitude": _dec(d["home_base_latitude"]),
                "home_base_longitude": _dec(d["home_base_longitude"]),
                "rating": _dec(d["rating"]) if reviews else None,
                "reviews_count": reviews,
                "created_at": _dt(d["created_at"]),
                "updated_at": _dt(d["updated_at"]),
            }
        )
    doctor_ids = {d["id"] for d in doctors}
    links = [
        {"doctor_id": _u(link["doctor_id"]), "specialization_id": _u(link["specialization_id"])}
        for link in snap.doctor_specializations
        if _u(link["doctor_id"]) in doctor_ids
    ]
    for doc in snap.documents:
        # Monolitda fayl lokal PRIVATE_MEDIA_ROOT da; file-service'ga ko'chirilmagan — ID yo'q
        skipped[f"catalog.doctor_documents: {doc['kind']} — file-service ID yo'q (fayl ko'chirilmagan)"] += 1
    memberships = [
        {
            "id": _u(m["id"]),
            "user_id": _u(m["user_id"]),
            "clinic_id": _u(m["clinic_id"]),
            "is_active": bool(m["is_active"]),
            "created_at": _dt(m["created_at"]),
            "updated_at": _dt(m["updated_at"]),
        }
        for m in snap.memberships
    ]
    affiliations = []
    for a in snap.affiliations:
        if a["status"] not in AFFILIATION_STATUS:
            skipped[f"catalog.affiliations: noma'lum holat '{a['status']}'"] += 1
            continue
        affiliations.append(
            {
                "id": _u(a["id"]),
                "doctor_id": _u(a["doctor_id"]),
                "clinic_id": _u(a["clinic_id"]),
                "position": a["position"] or "",
                "status": a["status"],
                "invited_by": _u(a["invited_by_id"]),
                "responded_at": _dt(a["responded_at"]),
                "created_at": _dt(a["created_at"]),
                "updated_at": _dt(a["updated_at"]),
            }
        )
    services = []
    for s in snap.services:
        price = tiyin(s["price"])
        if not price or price <= 0:
            skipped["catalog.services: narx 0 yoki yo'q (servisda price_tiyin > 0)"] += 1
            continue
        services.append(
            {
                "id": _u(s["id"]),
                "clinic_id": _u(s["clinic_id"]),
                "doctor_id": _u(s["doctor_id"]),
                "specialization_id": _u(s["specialization_id"]),
                "name": s["name"],
                "name_ru": s["name_ru"] or "",
                "description": s["description"] or "",
                "place": s["place"],
                "duration_minutes": int(s["duration_minutes"]),
                "price_tiyin": price,
                "is_active": bool(s["is_active"]),
                "mxik_code": s["mxik_code"] or "",
                "package_code": s["package_code"] or "",
                "vat_percent": s["vat_percent"],
                "created_at": _dt(s["created_at"]),
                "updated_at": _dt(s["updated_at"]),
            }
        )
    history = [
        {
            "service_id": _u(h["service_id"]),
            "old_price_tiyin": tiyin(h["old_price"]),
            "new_price_tiyin": tiyin(h["new_price"]),
            "changed_by": _u(h["changed_by_id"]),
            "is_large_change": bool(h["is_large_change"]),
            "created_at": _dt(h["created_at"]),
        }
        for h in snap.price_history
    ]
    return {
        "specializations": specializations,
        "clinics": clinics,
        "rooms": rooms,
        "doctors": doctors,
        "doctor_specializations": links,
        "clinic_memberships": memberships,
        "affiliations": affiliations,
        "services": services,
        "service_price_history": history,
    }


# --- schedule ---------------------------------------------------------------------------------------------


def _bookings_by_slot(snap: Snapshot) -> dict[uuid.UUID, list[dict[str, Any]]]:
    by_slot: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for b in snap.bookings:
        by_slot[_u(b["slot_id"])].append(b)  # type: ignore[index]
    for rows in by_slot.values():
        rows.sort(key=lambda r: str(r["created_at"]), reverse=True)
    return by_slot


def _slot_state(slot: dict[str, Any], bookings: list[dict[str, Any]], now: datetime) -> tuple[str, Any, Any] | None:
    """(status, hold_ref, hold_expires_at) yoki None (qator tashlanadi). Bron holati manba haqiqati."""
    live = next((b for b in bookings if b["status"] in LIVE), None)
    if live is not None:
        if live["status"] == "confirmed":
            return "booked", str(_u(live["id"])), None
        # Muddati o'tgan hold ham saqlanadi: lazy expiry uni bo'sh deb biladi, booking expire job bronni yopadi
        expires = _dt(slot["hold_expires_at"]) or now
        return "held", str(_u(live["id"])), expires
    if slot["status"] == "booked":
        visited = next((b for b in bookings if b["status"] in VISITED), None)
        return None if visited is None else ("booked", str(_u(visited["id"])), None)
    if slot["status"] == "held":
        return "free", None, None  # egasiz hold — muddati o'tgan deb hisoblanadi
    return slot["status"], None, None


def plan_schedule(snap: Snapshot, skipped: Counter[str], now: datetime) -> Plan:
    doctors = [
        {
            "doctor_id": _u(d["id"]),
            "user_id": _u(d["user_id"]),
            "status": d["status"],
            "accepts_home_visits": bool(d["accepts_home_visits"]),
            "changed_at": _dt(d["updated_at"]),
        }
        for d in snap.doctors
        if d["status"] in ("approved", "suspended")
    ]
    clinics = [
        {"clinic_id": _u(c["id"]), "status": c["status"], "changed_at": _dt(c["updated_at"])}
        for c in snap.clinics
        if c["status"] in ("active", "suspended")
    ]
    affiliations = [
        {
            "affiliation_id": _u(a["id"]),
            "doctor_id": _u(a["doctor_id"]),
            "clinic_id": _u(a["clinic_id"]),
            "status": a["status"],
            "changed_at": _dt(a["updated_at"]),
        }
        for a in snap.affiliations
        if a["status"] in ("active", "paused", "ended")
    ]
    rules = []
    for r in snap.rules:
        start, end = _t(r["start_time"]), _t(r["end_time"])
        valid_from, valid_to = _d(r["valid_from"]), _d(r["valid_to"])
        if start is None or end is None or end <= start or (valid_to and valid_from and valid_to < valid_from):
            skipped["schedule.working_rules: noto'g'ri vaqt oralig'i"] += 1
            continue
        rules.append(
            {
                "id": _u(r["id"]),
                "doctor_id": _u(r["doctor_id"]),
                "clinic_id": _u(r["clinic_id"]),
                "room_id": _u(r["room_id"]),
                "weekday": int(r["weekday"]),
                "start_time": start,
                "end_time": end,
                "slot_minutes": int(r["slot_minutes"]),
                "valid_from": valid_from,
                "valid_to": valid_to,
                "created_at": _dt(r["created_at"]),
                "updated_at": _dt(r["updated_at"]),
            }
        )
    exceptions = [
        {
            "id": _u(e["id"]),
            "doctor_id": _u(e["doctor_id"]),
            "date": _d(e["date"]),
            "reason": (e["reason"] or "")[:255],
            "created_at": _dt(e["created_at"]),
        }
        for e in snap.exceptions
    ]
    closures = [
        {
            "id": _u(c["id"]),
            "clinic_id": _u(c["clinic_id"]),
            "date": _d(c["date"]),
            "reason": (c["reason"] or "")[:255],
            "created_by": None,
            "created_at": _dt(c["created_at"]),
        }
        for c in snap.closures
    ]
    time_offs = [
        {
            "id": _u(t["id"]),
            "doctor_id": _u(t["doctor_id"]),
            "start_at": _dt(t["start_at"]),
            "end_at": _dt(t["end_at"]),
            "kind": t["kind"] or "other",
            "reason": (t["reason"] or "")[:255],
            "created_at": _dt(t["created_at"]),
        }
        for t in snap.time_offs
    ]
    price_rules = []
    for p in snap.price_rules:
        start, end = _t(p["start_time"]), _t(p["end_time"])
        weekdays = p["weekdays"] or ""
        if start == end or not set(weekdays) <= set("0123456") or not -90 <= int(p["percent"]) <= 100:
            skipped["schedule.price_rules: noto'g'ri qoida (vaqt/kunlar/foiz)"] += 1
            continue
        price_rules.append(
            {
                "id": _u(p["id"]),
                "doctor_id": _u(p["doctor_id"]),
                "name": p["name"] or "",
                "weekdays": weekdays,
                "start_time": start,
                "end_time": end,
                "percent": int(p["percent"]),
                "priority": int(p["priority"]),
                "is_active": bool(p["is_active"]),
                "created_at": _dt(p["created_at"]),
                "updated_at": _dt(p["updated_at"]),
            }
        )

    by_slot = _bookings_by_slot(snap)
    slots: list[dict[str, Any]] = []
    for s in snap.slots:
        sid = _u(s["id"])
        bookings = by_slot.get(sid, [])  # type: ignore[arg-type]
        state = _slot_state(s, bookings, now)
        if state is None:
            skipped["schedule.slots: 'booked', lekin bron yo'q (generatsiya qayta yaratadi)"] += 1
            continue
        status, hold_ref, hold_expires_at = state
        if s["status"] == "held" and status == "free":
            skipped["schedule.slots: egasiz 'held' → free (qator ko'chdi)"] += 1
        slots.append(
            {
                "id": sid,
                "doctor_id": _u(s["doctor_id"]),
                "clinic_id": _u(s["clinic_id"]),
                "room_id": _u(s["room_id"]),
                "start_at": _dt(s["start_at"]),
                "end_at": _dt(s["end_at"]),
                "status": status,
                "hold_ref": hold_ref,
                "hold_expires_at": hold_expires_at,
                "block_reason": (s["block_reason"] or "") if status == "blocked" else "",
                "block_note": (s["block_note"] or "")[:255] if status == "blocked" else "",
                "time_off_id": _u(s["time_off_id"]) if status == "blocked" else None,
                "blocked_by_slot_id": None,
                "travel_buffer_minutes": s["travel_buffer_minutes"] if status in ("held", "booked") else None,
                "ever_held": bool(bookings),
                "created_at": _dt(s["created_at"]),
                "updated_at": _dt(s["updated_at"]),
            }
        )
    _link_travel_buffers(slots, skipped)
    return {
        "doctors": doctors,
        "clinics": clinics,
        "affiliations": affiliations,
        "working_rules": rules,
        "schedule_exceptions": exceptions,
        "clinic_closures": closures,
        "time_offs": time_offs,
        "price_rules": price_rules,
        "slots": slots,
    }


def _link_travel_buffers(slots: list[dict[str, Any]], skipped: Counter[str]) -> None:
    """ADR-0011 #4: monolitda yo'l bloki egasi yozilmagan. Eng yaqin band uy chaqiruvi slotini topamiz;
    topilmasa — blok yetim (monolit xatosi), slot bo'shatiladi."""
    owners: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for s in slots:
        if s["clinic_id"] is None and s["status"] in ("held", "booked"):
            owners[s["doctor_id"]].append(s)
    for s in slots:
        if s["status"] != "blocked" or s["block_reason"] != "travel_buffer":
            continue
        best, best_gap = None, None
        for owner in owners.get(s["doctor_id"], []):
            buffer = timedelta(minutes=owner["travel_buffer_minutes"] or DEFAULT_BUFFER_MINUTES)
            if s["start_at"] < owner["end_at"] + buffer and s["end_at"] > owner["start_at"] - buffer:
                gap = abs((s["start_at"] - owner["start_at"]).total_seconds())
                if best_gap is None or gap < best_gap:
                    best, best_gap = owner, gap
        if best is None:
            s.update(status="free", block_reason="", block_note="")
            skipped["schedule.slots: egasiz travel_buffer bloki → free (qator ko'chdi)"] += 1
        else:
            s["blocked_by_slot_id"] = best["id"]


# --- pricing ----------------------------------------------------------------------------------------------


def plan_pricing(snap: Snapshot, skipped: Counter[str]) -> Plan:
    services_cache = [
        {
            "service_id": _u(s["id"]),
            "doctor_id": _u(s["doctor_id"]),
            "clinic_id": _u(s["clinic_id"]),
            "specialization_id": _u(s["specialization_id"]),
            "name": s["name"],
            "name_ru": s["name_ru"] or "",
            "place": s["place"],
            "duration_minutes": int(s["duration_minutes"]),
            "price_tiyin": tiyin(s["price"]),
            "is_active": bool(s["is_active"]),
            "mxik_code": s["mxik_code"] or "",
            "package_code": s["package_code"] or "",
            "vat_percent": s["vat_percent"],
            "changed_at": _dt(s["updated_at"]),
        }
        for s in snap.services
        if (tiyin(s["price"]) or 0) > 0
    ]
    client_bookings = [
        {
            "booking_id": _u(b["id"]),
            "client_id": _u(b["client_id"]),
            "doctor_id": _u(b["doctor_id"]),
            "status": b["status"],
            "no_show_by": b["no_show_by"] or "",
            "completed_at": _dt(b["completed_at"]),
            "changed_at": _dt(b["updated_at"]),
        }
        for b in snap.bookings
        if b["status"] in BOOKING_STATUS
    ]
    promos = []
    for p in snap.promos:
        if p["kind"] not in ("percent", "fixed"):
            skipped[f"pricing.promo_codes: noma'lum tur '{p['kind']}'"] += 1
            continue
        value = _dec(p["value"]) or Decimal(0)
        promos.append(
            {
                "id": _u(p["id"]),
                "code": str(p["code"]).strip().upper(),
                "kind": p["kind"],
                # percent: 1..100 (butun); fixed: tiyin
                "value": int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
                if p["kind"] == "percent"
                else tiyin(value),
                "max_discount_tiyin": tiyin(p["max_discount"]),
                "min_amount_tiyin": tiyin(p["min_amount"]) or 0,
                "valid_from": _dt(p["valid_from"]),
                "valid_until": _dt(p["valid_until"]),
                "max_uses": p["max_uses"],
                "max_uses_per_user": int(p["max_uses_per_user"] or 1),
                "first_booking_only": bool(p["first_booking_only"]),
                "doctor_id": _u(p["doctor_id"]),
                "borne_by": p["borne_by"] or "platform",
                "is_active": bool(p["is_active"]),
                "created_at": _dt(p["created_at"]),
                "updated_at": _dt(p["updated_at"]),
            }
        )
    redemptions = [
        {
            "id": _u(r["id"]),
            "promo_id": _u(r["promo_id"]),
            "booking_id": _u(r["booking_id"]),
            "client_id": _u(r["client_id"]),
            "amount_tiyin": tiyin(r["amount"]) or 0,
            "state": "active" if r["is_active"] else "released",
            "reserved_at": _dt(r["created_at"]),
            "updated_at": _dt(r["updated_at"]),
        }
        for r in snap.redemptions
    ]
    packages = [
        {
            "id": _u(p["id"]),
            "doctor_id": _u(p["doctor_id"]),
            "service_id": _u(p["service_id"]),
            "name": p["name"],
            "sessions": int(p["sessions"]),
            "discount_percent": int(p["discount_percent"]),
            "validity_days": int(p["validity_days"] or 90),
            "is_active": bool(p["is_active"]),
            "created_at": _dt(p["created_at"]),
        }
        for p in snap.packages
    ]
    names = {p["id"]: p["name"] for p in packages}
    enrollments = []
    for e in snap.enrollments:
        package_id = _u(e["package_id"])
        if package_id not in names:
            skipped["pricing.package_enrollments: paket topilmadi"] += 1
            continue
        enrollments.append(
            {
                "id": _u(e["id"]),
                "package_id": package_id,
                "client_id": _u(e["client_id"]),
                "doctor_id": _u(e["doctor_id"]),
                "service_id": _u(e["service_id"]),
                "package_name": names[package_id],
                "sessions_total": int(e["sessions_total"]),
                "discount_percent": int(e["discount_percent"]),
                "expires_at": _dt(e["expires_at"]),
                "is_active": bool(e["is_active"]),
                "created_at": _dt(e["created_at"]),
            }
        )
    enrolled = {e["id"] for e in enrollments}
    uses = []
    for u in snap.package_uses:
        if _u(u["enrollment_id"]) not in enrolled:
            skipped["pricing.package_uses: yozilish ko'chmagan"] += 1
            continue
        uses.append(
            {
                "id": _u(u["id"]),
                "enrollment_id": _u(u["enrollment_id"]),
                "booking_id": _u(u["booking_id"]),
                "amount_tiyin": tiyin(u["amount"]) or 0,
                "state": "active" if u["is_active"] else "released",
                "reserved_at": _dt(u["created_at"]),
                "updated_at": _dt(u["updated_at"]),
            }
        )
    return {
        "services_cache": services_cache,
        "client_bookings": client_bookings,
        "promo_codes": promos,
        "promo_redemptions": redemptions,
        "packages": packages,
        "package_enrollments": enrollments,
        "package_uses": uses,
    }


# --- booking ----------------------------------------------------------------------------------------------


def _patient_snapshot(p: dict[str, Any] | None) -> dict[str, Any]:
    """booking-service `create._persist` bilan bir xil kalitlar (user-service internal javobi shakli)."""
    if p is None:
        return {"full_name": "", "relation": "", "birth_date": None, "gender": "", "weight_kg": None}
    birth = _d(p["birth_date"])
    return {
        "full_name": p["full_name"] or "",
        "relation": p["relation"] or "",
        "birth_date": birth.isoformat() if birth else None,
        "gender": p["gender"] or "",
        "weight_kg": p["weight_kg"],
    }


def _address_snapshot(a: dict[str, Any]) -> dict[str, Any]:
    keys = ("label", "city", "district", "street", "entrance", "floor", "apartment", "comment")
    return {**{k: a[k] or "" for k in keys}, "latitude": str(a["latitude"]), "longitude": str(a["longitude"])}


def plan_booking(snap: Snapshot, skipped: Counter[str]) -> Plan:
    slots = {_u(s["id"]): s for s in snap.slots}
    patients = {_u(p["id"]): p for p in snap.patients}
    addresses = {_u(a["id"]): a for a in snap.addresses}
    paid: dict[uuid.UUID, int] = defaultdict(int)
    last_payment: dict[uuid.UUID, tuple[str, uuid.UUID]] = {}
    for pay in snap.payments:
        if pay["status"] not in PAID_STATUSES:
            continue
        bid = _u(pay["booking_id"])
        paid[bid] += tiyin(pay["amount"]) or 0  # type: ignore[index]
        stamp = str(pay["updated_at"])
        if bid not in last_payment or stamp > last_payment[bid][0]:  # type: ignore[operator]
            last_payment[bid] = (stamp, _u(pay["id"]))  # type: ignore[index,assignment]
    refunded: dict[uuid.UUID, int] = defaultdict(int)
    for ref in snap.refunds:
        if ref["status"] == "succeeded":
            refunded[_u(ref["booking_id"])] += tiyin(ref["amount"]) or 0  # type: ignore[index]

    bookings = []
    for b in snap.bookings:
        bid = _u(b["id"])
        slot = slots.get(_u(b["slot_id"]))
        if slot is None:
            skipped["booking.bookings: slot topilmadi"] += 1
            continue
        if b["status"] not in BOOKING_STATUS:
            skipped[f"booking.bookings: noma'lum holat '{b['status']}'"] += 1
            continue
        if _u(b["patient_id"]) not in patients:
            skipped["booking.bookings: bemor topilmadi — snapshot bo'sh (qator ko'chdi)"] += 1
        status = b["status"]
        if status == "confirmed" and b["started_at"]:
            status = "in_progress"  # monolitda alohida holat yo'q edi — `started_at` belgisi
        place = "clinic" if slot["clinic_id"] else "home"
        address = addresses.get(_u(b["address_id"])) if place == "home" else None
        total = tiyin(b["total_price"]) or 0
        discount = tiyin(b["discount_amount"]) or 0
        original = tiyin(b["original_price"])
        updated = _dt(b["updated_at"])
        bookings.append(
            {
                "id": bid,
                "number": b["number"],
                "client_id": _u(b["client_id"]),
                "patient_id": _u(b["patient_id"]),
                "doctor_id": _u(b["doctor_id"]),
                "clinic_id": _u(slot["clinic_id"]),
                "slot_id": _u(b["slot_id"]),
                "address_id": _u(b["address_id"]),
                "place": place,
                "start_at": _dt(slot["start_at"]),
                "end_at": _dt(slot["end_at"]),
                "status": status,
                "quote_id": None,
                "payment_mode": b["payment_mode"] or "prepaid",
                "payment_due_at": _dt(slot["hold_expires_at"]) if status == "pending_payment" else None,
                "original_tiyin": original if original is not None else total + discount,
                "price_adjust_percent": int(b["price_adjust_percent"] or 0),
                "discount_kind": b["discount_kind"] or "",
                "discount_tiyin": discount,
                "discount_borne_by": b["discount_borne_by"] or "",
                "promo_code": b["promo_code"] or "",
                "total_tiyin": total,
                "prepay_tiyin": tiyin(b["prepay_amount"]) or 0,
                "commission_bp": bp(b["commission_rate"]),
                "provider_fee_bp": bp(b["provider_fee_rate"]),
                "platform_fee_tiyin": tiyin(b["platform_fee"]) or 0,
                "provider_fee_tiyin": tiyin(b["provider_fee"]) or 0,
                "doctor_payout_tiyin": tiyin(b["doctor_payout"]) or 0,
                "paid_tiyin": paid.get(bid, 0),  # type: ignore[arg-type]
                "refunded_tiyin": refunded.get(bid, 0),  # type: ignore[arg-type]
                "payment_id": last_payment[bid][1] if bid in last_payment else None,
                "patient": _patient_snapshot(patients.get(_u(b["patient_id"]))),
                "address": _address_snapshot(address) if address else None,
                "client_comment": b["client_comment"] or "",
                "note": b["note"] or "",
                "confirmed_at": None,  # monolitda yozilmagan
                "started_at": _dt(b["started_at"]),
                "completed_at": _dt(b["completed_at"]),
                "completed_by": b["completed_by"] or "",
                "no_show_by": b["no_show_by"] or "",
                "cancelled_at": updated if status == "cancelled" else None,
                "cancelled_by": "",
                "cancel_reason_code": "",
                "cancel_reason_text": b["cancelled_reason"] or "",
                "rescheduled_count": int(b["rescheduled_count"] or 0),
                "created_at": _dt(b["created_at"]),
                "updated_at": updated,
            }
        )
    known = {b["id"] for b in bookings}
    items = []
    for i in snap.items:
        if _u(i["booking_id"]) not in known:
            continue  # bron o'zi tashlangan (yuqorida sanalgan)
        price = tiyin(i["price"]) or 0
        items.append(
            {
                "booking_id": _u(i["booking_id"]),
                "service_id": _u(i["service_id"]),
                "service_name": i["service_name"],
                "base_price_tiyin": price,  # monolit faqat yakuniy narxni saqlagan
                "price_tiyin": price,
                "duration_minutes": int(i["duration_minutes"]),
                "mxik_code": i["mxik_code"] or "",
                "package_code": i["package_code"] or "",
                "vat_percent": i["vat_percent"],
            }
        )
    waitlist = [
        {
            "id": _u(w["id"]),
            "client_id": _u(w["client_id"]),
            "doctor_id": _u(w["doctor_id"]),
            "date_from": _d(w["date_from"]),
            "date_to": _d(w["date_to"]),
            "place": w["place"],
            "status": w["status"],
            "notified_at": _dt(w["notified_at"]),
            "notified_count": int(w["notified_count"] or 0),
            "created_at": _dt(w["created_at"]),
        }
        for w in snap.waitlist
    ]
    return {"bookings": bookings, "booking_items": items, "waitlist_entries": waitlist}


# --- yozish -----------------------------------------------------------------------------------------------

# Natural kalit (bitta jadvalga bir nechta UNIQUE bo'lsa ham ON CONFLICT DO NOTHING hammasini yutadi)
KEYS = {
    "doctor_specializations": "doctor_id",
    "doctors": "doctor_id",  # schedule proyeksiyasi; catalog'da `id` (pastda)
    "clinics": "clinic_id",
    "affiliations": "affiliation_id",
    "services_cache": "service_id",
    "client_bookings": "booking_id",
}
# Bu jadvallarda tabiiy kalit yo'q (identity PK) — faqat SHU ishga tushishda yangi qo'shilgan egasi uchun
DEPENDENT = {"service_price_history": ("services", "service_id"), "booking_items": ("bookings", "booking_id")}


def _put(conn: Connection, table_name: str, rows: Iterable[dict[str, Any]], key: str) -> list[Any]:
    rows = list(rows)
    if not rows:
        return []
    table = Table(table_name, MetaData(), autoload_with=conn)
    inserted: list[Any] = []
    for start in range(0, len(rows), 500):
        stmt = (
            insert(table)
            .values(rows[start : start + 500])
            .on_conflict_do_nothing()
            .returning(table.c[key])  # rowcount ko'p qatorli INSERT'da ishonchsiz (-1)
        )
        inserted.extend(r[0] for r in conn.execute(stmt).all())
    return inserted


def load(engine: Engine, plan: Plan, *, id_keys: dict[str, str] | None = None) -> dict[str, int]:
    keys = {**KEYS, **(id_keys or {})}
    out: dict[str, int] = {}
    new_owners: dict[str, set[Any]] = {}
    with engine.begin() as conn:
        for table, rows in plan.items():
            if table in DEPENDENT:
                owner, column = DEPENDENT[table]
                fresh = new_owners.get(owner, set())
                rows = [r for r in rows if r[column] in fresh]
                ids = _put(conn, table, rows, "id")
            else:
                ids = _put(conn, table, rows, keys.get(table, "id"))
            new_owners[table] = set(ids)
            out[table] = len(ids)
    return out


def run(args: argparse.Namespace) -> int:
    snap = extract(_engine(args.source))
    now = datetime.now(UTC)
    skipped: Counter[str] = Counter()
    plans = {
        "catalog": plan_catalog(snap, skipped),
        "schedule": plan_schedule(snap, skipped, now),
        "pricing": plan_pricing(snap, skipped),
        "booking": plan_booking(snap, skipped),
    }
    report: dict[str, Any] = {
        "source": {k: len(v) for k, v in vars(snap).items()},
        "plan": {name: {t: len(rows) for t, rows in plan.items()} for name, plan in plans.items()},
        "skipped": dict(sorted(skipped.items())),
    }
    if not args.dry_run:
        targets = {name: getattr(args, name) for name in plans if getattr(args, name)}
        if not targets:
            print(
                "XATO: hech bir maqsad baza ko'rsatilmagan (--catalog/--schedule/--pricing/--booking)", file=sys.stderr
            )
            return 2
        report["inserted"] = {}
        for name, url in targets.items():
            # catalog `doctors`/`clinics`/`affiliations` — `id`; schedule proyeksiyalari — `<obyekt>_id`
            id_keys = {"doctors": "id", "clinics": "id", "affiliations": "id"} if name == "catalog" else None
            report["inserted"][name] = load(_engine(url), plans[name], id_keys=id_keys)
        report["not_targeted"] = [name for name in plans if name not in targets]
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.environ.get("MONOLITH_DATABASE_URL"))
    for name in ("catalog", "schedule", "pricing", "booking"):
        parser.add_argument(f"--{name}", default=os.environ.get(f"{name.upper()}_DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.source:
        parser.error("--source (yoki MONOLITH_DATABASE_URL) majburiy")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
