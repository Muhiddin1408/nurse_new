#!/usr/bin/env python3
"""5-bosqich: monolit → review-service (sharhlar), dispute-service (nizolar) — bir martalik ko'chirish (ADR-0013).

O'SHA UUID'lar bilan. Idempotent (`ON CONFLICT DO NOTHING`). search (Elasticsearch) va analytics (ClickHouse)
ko'chirilmaydi: search — admin `POST /v1/admin/search/reindex` bilan catalog'dan quriladi; analytics — faqat
cutover'dan keyingi eventlar (tarixiy hisobot — monolit zaxirasidan).

    python scripts/migrate/feedback.py --dry-run --source sqlite:///db.sqlite3
    python scripts/migrate/feedback.py --source postgresql://monolith@.../medbron \\
        --review postgresql://review_service:...@127.0.0.1:5412/review_service \\
        --dispute postgresql://dispute_service:...@127.0.0.1:5413/dispute_service

Shartlar: maqsad bazalarda migratsiyalar bajarilgan, ikkala servisning event-consumer'i TO'XTATILGAN.
Reyting: catalog'dagi `rating`/`reviews_count` monolitdan 3-bosqichda ko'chgan — qayta hisoblash shart emas;
keyingi har sharh o'zgarishi `rating.recalculated` bilan yangilaydi. Runbook: docs/runbooks/realtime-cutover.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import _dt, _engine, _json, _put, _rows, _u, tiyin  # noqa: E402

REVIEW_STATUS = {"published", "pending", "rejected"}
DISPUTE_REASONS = {"doctor_no_show", "poor_service", "wrong_charge", "other"}
PAID = ("succeeded", "partially_refunded", "refunded")

QUERIES = {
    "reviews": "SELECT r.id, r.booking_id, r.client_id, r.doctor_id, r.rating, r.comment, r.is_anonymous, r.status,"
    " r.moderation_flags, r.moderated_by_id, r.moderated_at, r.doctor_reply, r.doctor_replied_at, r.created_at,"
    " r.updated_at, u.full_name FROM booking_review r JOIN account_user u ON u.id = r.client_id",
    "disputes": "SELECT id, booking_id, raised_by_id, reason, description, status, due_at, resolution_note,"
    " resolved_by_id, resolved_at, refund_id, created_at, updated_at FROM booking_dispute",
    "bookings": "SELECT b.id, b.number, b.client_id, b.doctor_id, b.status, b.completed_at, b.updated_at, s.clinic_id"
    " FROM booking_booking b JOIN schedule_timeslot s ON s.id = b.slot_id",
    "payments": "SELECT booking_id, amount, status FROM payment_payment",
    "refunds": "SELECT id, amount, status, completed_at FROM payment_refund",
}


@dataclass
class Snapshot:
    reviews: list[dict[str, Any]] = field(default_factory=list)
    disputes: list[dict[str, Any]] = field(default_factory=list)
    bookings: list[dict[str, Any]] = field(default_factory=list)
    payments: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)


Plan = dict[str, list[dict[str, Any]]]


def extract(engine: Engine) -> Snapshot:
    with engine.connect() as conn:
        return Snapshot(**{name: _rows(conn, sql) for name, sql in QUERIES.items()})


def short_name(full_name: str | None) -> str:
    """"Aziz Tursunov" → "Aziz T." (review-service ommaviy ko'rinishi)."""
    parts = (full_name or "").split()
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[1][0]}."


def plan_review(snap: Snapshot, skipped: Counter[str]) -> Plan:
    bookings = {_u(b["id"]): b for b in snap.bookings}
    reviewable = [
        {
            "booking_id": _u(b["id"]), "number": b["number"], "client_id": _u(b["client_id"]),
            "doctor_id": _u(b["doctor_id"]), "clinic_id": _u(b["clinic_id"]),
            "completed_at": _dt(b["completed_at"] or b["updated_at"]),
        }  # fmt: skip
        for b in snap.bookings
        if b["status"] == "completed"
    ]
    reviews = []
    for r in snap.reviews:
        b = bookings.get(_u(r["booking_id"]))
        if b is None:
            skipped["reviews: bron topilmadi"] += 1
            continue
        if r["status"] not in REVIEW_STATUS:
            skipped[f"reviews: noma'lum holat {r['status']!r}"] += 1
            continue
        reviews.append({
            "id": _u(r["id"]), "booking_id": _u(b["id"]), "booking_number": b["number"], "client_id": _u(r["client_id"]),
            "doctor_id": _u(r["doctor_id"]), "clinic_id": _u(b["clinic_id"]), "rating": int(r["rating"]),
            "comment": r["comment"] or "", "is_anonymous": bool(r["is_anonymous"]),
            "author_name": "" if r["is_anonymous"] else short_name(r["full_name"]), "status": r["status"],
            "moderation_flags": _json(r["moderation_flags"], []), "moderated_by": _u(r["moderated_by_id"]),
            "moderated_at": _dt(r["moderated_at"]), "doctor_reply": r["doctor_reply"] or "",
            "doctor_replied_at": _dt(r["doctor_replied_at"]), "created_at": _dt(r["created_at"]),
            "updated_at": _dt(r["updated_at"]),
        })  # fmt: skip
    return {"reviewable_bookings": reviewable, "reviews": reviews}


def plan_dispute(snap: Snapshot, skipped: Counter[str], notes: Counter[str]) -> Plan:
    bookings = {_u(b["id"]): b for b in snap.bookings}
    paid: dict[Any, int] = defaultdict(int)
    for p in snap.payments:
        if p["status"] in PAID:
            paid[_u(p["booking_id"])] += tiyin(p["amount"]) or 0
    refunds = {_u(r["id"]): r for r in snap.refunds}

    latest: dict[Any, dict[str, Any]] = {}
    for d in sorted(snap.disputes, key=lambda d: str(d["created_at"])):
        bid = _u(d["booking_id"])
        if bid in latest:
            skipped["disputes: bronda bir nechta nizo — faqat oxirgisi"] += 1
        latest[bid] = d

    disputes = []
    for bid, d in latest.items():
        b = bookings.get(bid)
        if b is None:
            skipped["disputes: bron topilmadi"] += 1
            continue
        reason = d["reason"] if d["reason"] in DISPUTE_REASONS else "other"
        paid_tiyin = paid.get(bid, 0)
        refund = refunds.get(_u(d["refund_id"])) if d["refund_id"] else None
        row: dict[str, Any] = {
            "id": _u(d["id"]), "booking_id": bid, "booking_number": b["number"], "client_id": _u(d["raised_by_id"]),
            "doctor_id": _u(b["doctor_id"]), "paid_tiyin": paid_tiyin, "reason": reason,
            "description": d["description"] or "", "due_at": _dt(d["due_at"]), "resolution_note": d["resolution_note"] or "",
            "resolved_by": _u(d["resolved_by_id"]), "resolved_at": _dt(d["resolved_at"]), "refund_id": _u(d["refund_id"]),
            "created_at": _dt(d["created_at"]), "updated_at": _dt(d["updated_at"]),
            "status": "open", "resolution": None, "refund_tiyin": 0, "doctor_liable": None, "refunded_tiyin": 0,
            "closed_at": None,
        }  # fmt: skip
        if d["status"] == "resolved_client" and refund is not None:
            amount = tiyin(refund["amount"]) or 0
            done = refund["status"] == "succeeded"
            row |= {
                "resolution": "refund_full" if amount >= paid_tiyin else "refund_partial", "refund_tiyin": amount,
                "doctor_liable": True,  # monolitda refund doim shifokor ulushidan ham ushlangan
                "status": "closed" if done else "resolved", "refunded_tiyin": amount if done else 0,
                "closed_at": _dt(refund["completed_at"]) if done else None,
            }  # fmt: skip
        elif d["status"] in ("resolved_client", "resolved_doctor"):
            row |= {"resolution": "rejected", "doctor_liable": False, "status": "closed", "closed_at": _dt(d["resolved_at"])}
            if d["status"] == "resolved_client":
                notes["disputes: mijoz foydasiga, lekin refundsiz → rejected/closed"] += 1
        disputes.append(row)
    return {"disputes": disputes}


def load(engine: Engine, plan: Plan, keys: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    with engine.begin() as conn:
        for table, rows in plan.items():
            out[table] = len(_put(conn, table, rows, keys.get(table, "id")))
    return out


def run(args: argparse.Namespace) -> int:
    snap = extract(_engine(args.source))
    skipped: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    plans = {"review": plan_review(snap, skipped), "dispute": plan_dispute(snap, skipped, notes)}
    report: dict[str, Any] = {
        "source": {k: len(v) for k, v in vars(snap).items()},
        "plan": {name: {t: len(rows) for t, rows in plan.items()} for name, plan in plans.items()},
        "skipped": dict(sorted(skipped.items())),
        "notes": dict(sorted(notes.items())),
    }
    if not args.dry_run:
        targets = {name: getattr(args, name) for name in plans if getattr(args, name)}
        if not targets:
            print("XATO: hech bir maqsad baza ko'rsatilmagan (--review/--dispute)", file=sys.stderr)
            return 2
        keys = {"reviewable_bookings": "booking_id"}
        report["inserted"] = {name: load(_engine(url), plans[name], keys) for name, url in targets.items()}
        report["not_targeted"] = [name for name in plans if name not in targets]
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.environ.get("MONOLITH_DATABASE_URL"))
    for name in ("review", "dispute"):
        parser.add_argument(f"--{name}", default=os.environ.get(f"{name.upper()}_DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.source:
        parser.error("--source (yoki MONOLITH_DATABASE_URL) majburiy")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
