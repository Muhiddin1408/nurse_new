#!/usr/bin/env python3
"""4-bosqich: monolit → payment-service, billing-service — bir martalik ko'chirish (ADR-0012).

O'SHA UUID'lar bilan (to'lov, refund, ledger tranzaksiyasi, payout). Idempotent: `INSERT ... ON CONFLICT DO
NOTHING`; ledger yozuvlari faqat SHU ishga tushishda yangi qo'shilgan tranzaksiyalar uchun. Pul: so'm → tiyin.
Mos kelmaydigan qator jim tashlanmaydi — hisobotdagi `skipped`/`notes` da sabab bilan sanaladi.

MONOLIT QIYMATLARI O'ZGARTIRILMAYDI (ADR-0012 "Oqibatlar"): payout qatorlari va hali payout'ga kirmagan
daromadlar monolit formulasi bilan (`doctor_payout − (R − R × commission)`, ≥ 0) olinadi; migratsiya qilingan
refundning `doctor_share_tiyin` i ham shu formula — ko'chirilgan ledger bilan mos. Yangi formula faqat
ko'chirishdan keyingi hodisalarga.

    python scripts/migrate/money.py --dry-run --source sqlite:///db.sqlite3
    python scripts/migrate/money.py --source postgresql://monolith@.../medbron \\
        --payment postgresql://payment_service:...@127.0.0.1:5410/payment_service \\
        --billing postgresql://billing_service:...@127.0.0.1:5411/billing_service

Shartlar: maqsad bazalarda migratsiyalar bajarilgan; payment-service scheduler va ikkala servisning
event-consumer'i TO'XTATILGAN (ko'chirilgan pending refundlar yuborilmasin, eventlar aralashmasin).
Yakunda payment daftarining trial balance'i tekshiriladi (0 bo'lmasa — exit 3). Runbook: docs/runbooks/money-cutover.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import _d, _dt, _engine, _json, _put, _rows, _u, bp, tiyin  # noqa: E402

PAYMENT_STATUS = {
    "created", "processing", "succeeded", "failed", "cancelled", "expired", "refunded", "partially_refunded",
}  # fmt: skip
MONEY_TAKEN = ("succeeded", "partially_refunded", "refunded")
REFUND_STATUS = {"pending", "succeeded", "failed", "manual_required"}
LEDGER_KIND = {"payment_succeeded": "payment_captured", "refund_succeeded": "refund_succeeded", "payout_paid": "payout_paid"}
ACCOUNTS = {"client_payments", "platform_revenue", "doctor_payable", "provider_fees"}
INITIATOR = {"client", "doctor", "admin", "system"}
# B8 dan oldingi bronlarda taqsimot yo'q — monolit `current_rates()` standarti (admin-service standarti bilan bir xil)
DEFAULT_COMMISSION_BP, DEFAULT_PROVIDER_BP = 1500, 100
NS = uuid.UUID("7d0c6b2e-4f5a-4b8e-9a51-3f0c2a9e6d10")  # billing yozuvlari uchun deterministik ID

QUERIES = {
    "payments": "SELECT id, booking_id, provider, amount, status, external_id, provider_create_time, perform_time,"
    " cancel_time, cancel_reason, provider_state, needs_refund, created_at, updated_at FROM payment_payment",
    "refunds": "SELECT id, payment_id, booking_id, amount, reason, status, initiated_by, external_refund_id,"
    " attempts, next_attempt_at, last_error, completed_at, created_at, updated_at FROM payment_refund",
    "ledger_transactions": "SELECT id, kind, ref_type, ref_id, booking_id, description, created_at"
    " FROM payment_ledgertransaction",
    "ledger_entries": "SELECT transaction_id, account, debit, credit, doctor_id, created_at FROM payment_ledgerentry",
    "discrepancies": "SELECT id, type, provider, external_id, payment_id, booking_id, local_amount, provider_amount,"
    " details, period_date, status, resolution_note, fingerprint, created_at, updated_at"
    " FROM payment_paymentdiscrepancy",
    "accounts": "SELECT doctor_id, holder_name, bank_name, account_number, mfo, inn, updated_at"
    " FROM payment_payoutaccount",
    "payouts": "SELECT id, doctor_id, period_start, period_end, status, bookings_count, gross_amount, platform_fee,"
    " provider_fee, refunds_amount, net_amount, paid_at, paid_by_id, bank_reference, created_at, updated_at"
    " FROM payment_payoutperiod",
    "payout_lines": "SELECT payout_id, booking_id, gross_amount, platform_fee, provider_fee, refunds_amount,"
    " net_amount FROM payment_payoutline",
    "bookings": "SELECT b.id, b.number, b.status, b.client_id, b.doctor_id, b.total_price, b.platform_fee,"
    " b.provider_fee, b.doctor_payout, b.commission_rate, b.discount_amount, b.discount_borne_by, b.no_show_by,"
    " b.completed_at, s.clinic_id, s.start_at, s.end_at FROM booking_booking b JOIN schedule_timeslot s"
    " ON s.id = b.slot_id",
    "items": "SELECT booking_id, service_name, price, mxik_code, package_code, vat_percent, created_at, id"
    " FROM booking_bookingitem",
}


@dataclass
class Snapshot:
    payments: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)
    ledger_transactions: list[dict[str, Any]] = field(default_factory=list)
    ledger_entries: list[dict[str, Any]] = field(default_factory=list)
    discrepancies: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    payouts: list[dict[str, Any]] = field(default_factory=list)
    payout_lines: list[dict[str, Any]] = field(default_factory=list)
    bookings: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)


Plan = dict[str, list[dict[str, Any]]]


def extract(engine: Engine) -> Snapshot:
    with engine.connect() as conn:
        return Snapshot(**{name: _rows(conn, sql) for name, sql in QUERIES.items()})


def _int(value: Any) -> int | None:
    return None if value is None or value == "" else int(value)


def money_split(b: dict[str, Any], notes: Counter[str]) -> dict[str, Any]:
    """Bron snapshot'i → `money_split` (tiyin). B8 dan oldingi (stavkasiz) bronlar — standart stavkalar."""
    total = tiyin(b["total_price"]) or 0
    platform, provider, doctor = (tiyin(b[k]) or 0 for k in ("platform_fee", "provider_fee", "doctor_payout"))
    if platform + provider + doctor != total or (doctor == 0 and total > 0):
        notes["money: taqsimot yo'q/mos emas — standart stavkalar (1500/100 bp)"] += 1
        platform = (total * DEFAULT_COMMISSION_BP + 5_000) // 10_000
        provider = (total * DEFAULT_PROVIDER_BP + 5_000) // 10_000
        doctor = total - platform - provider
    return {
        "total_tiyin": total,
        "platform_fee_tiyin": platform,
        "provider_fee_tiyin": provider,
        "doctor_payout_tiyin": doctor,
        "discount_tiyin": tiyin(b["discount_amount"]) or 0,
        "discount_borne_by": b["discount_borne_by"] or "",
    }


def receipt_items(items: list[dict[str, Any]], amount: int) -> list[dict[str, Any]]:
    """payment-service `fiscal.receipt_items` bilan bir xil: proporsional (pastga), qoldiq oxirgisiga."""
    rows = sorted(items, key=lambda i: (str(i["created_at"]), str(i["id"])))
    total = sum(tiyin(i["price"]) or 0 for i in rows)
    out, running = [], 0
    for idx, i in enumerate(rows):
        price = tiyin(i["price"]) or 0
        if idx == len(rows) - 1:
            part = amount - running
        else:
            part = price * amount // total if total else 0
            running += part
        out.append({
            "title": str(i["service_name"])[:128], "price_tiyin": part, "count": 1, "code": i["mxik_code"] or "",
            "package_code": i["package_code"] or "", "vat_percent": int(i["vat_percent"] or 0),
        })  # fmt: skip
    return out


def monolith_doctor_share(refund_tiyin: int, commission_bp: int) -> int:
    """Monolit ledger'i: platforma qismi `R × commission` (half-up), shifokor — qolgani."""
    return refund_tiyin - (refund_tiyin * commission_bp + 5_000) // 10_000


def _refund_source(r: dict[str, Any]) -> str:
    reason = str(r["reason"] or "")
    if reason == "booking_unavailable":
        return "confirmation_failed"
    if reason.startswith("no_show"):
        return "booking_no_show"
    if r["initiated_by"] == "admin" or reason == "provider_cancelled":
        return "admin"
    return "booking_cancelled"


def plan_payment(snap: Snapshot, skipped: Counter[str], notes: Counter[str]) -> Plan:
    bookings = {_u(b["id"]): b for b in snap.bookings}
    items: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for i in snap.items:
        items[_u(i["booking_id"])].append(i)  # type: ignore[index]

    payments = []
    for p in snap.payments:
        b = bookings.get(_u(p["booking_id"]))
        if b is None:
            skipped["payments: bron topilmadi"] += 1
            continue
        if p["status"] not in PAYMENT_STATUS:
            skipped[f"payments: noma'lum holat {p['status']!r}"] += 1
            continue
        amount = tiyin(p["amount"]) or 0
        if amount <= 0:
            skipped["payments: summa 0"] += 1
            continue
        state = _json(p["provider_state"], {})
        status = p["status"]
        if status == "refunded" and p["provider"] == "payme" and p["cancel_time"]:
            status = "reversed"  # Payme CancelTransaction −2 (monolit buni ham `refunded` derdi)
            notes["payments: payme refunded + cancel_time → reversed"] += 1
        fiscal = state.get("fiscal") if isinstance(state.get("fiscal"), dict) else {}
        payments.append({
            "id": _u(p["id"]), "booking_id": _u(b["id"]), "booking_number": b["number"], "client_id": _u(b["client_id"]),
            "doctor_id": _u(b["doctor_id"]), "clinic_id": _u(b["clinic_id"]), "provider": p["provider"],
            "amount_tiyin": amount, "status": status, "external_id": p["external_id"] or "",
            "provider_create_time": _int(p["provider_create_time"]), "perform_time": _int(p["perform_time"]),
            "cancel_time": _int(p["cancel_time"]), "cancel_reason": _int(p["cancel_reason"]), "provider_state": state,
            "money": money_split(b, notes), "receipt_items": receipt_items(items.get(_u(b["id"]), []), amount),  # type: ignore[arg-type]
            "needs_refund": bool(p["needs_refund"]),
            "captured_at": _dt(p["updated_at"]) if p["status"] in MONEY_TAKEN else None,
            "fiscal_submitted_at": _dt(fiscal.get("at")) if fiscal.get("submitted") else None,
            "created_at": _dt(p["created_at"]), "updated_at": _dt(p["updated_at"]),
        })  # fmt: skip
    known = {p["id"] for p in payments}

    refunds = []
    for r in snap.refunds:
        if _u(r["payment_id"]) not in known:
            skipped["refunds: to'lov ko'chmadi"] += 1
            continue
        if r["status"] not in REFUND_STATUS:
            skipped[f"refunds: noma'lum holat {r['status']!r}"] += 1
            continue
        b = bookings[_u(r["booking_id"])]
        amount = tiyin(r["amount"]) or 0
        refunds.append({
            "id": _u(r["id"]), "payment_id": _u(r["payment_id"]), "booking_id": _u(r["booking_id"]),
            "amount_tiyin": amount,
            "doctor_share_tiyin": max(0, monolith_doctor_share(amount, bp(b["commission_rate"]) or DEFAULT_COMMISSION_BP)),
            "source": _refund_source(r), "source_ref": str(_u(r["id"])),  # monolit refund ID — to'qnashuvsiz
            "reason": str(r["reason"] or "")[:64], "initiated_by": r["initiated_by"] if r["initiated_by"] in INITIATOR else "system",
            "status": r["status"], "external_refund_id": (r["external_refund_id"] or "")[:64],
            "attempts": int(r["attempts"] or 0), "next_attempt_at": _dt(r["next_attempt_at"]),
            "last_error": r["last_error"] or "", "completed_at": _dt(r["completed_at"]),
            "created_at": _dt(r["created_at"]), "updated_at": _dt(r["updated_at"]),
        })  # fmt: skip

    transactions, entries = [], []
    for t in snap.ledger_transactions:
        kind = LEDGER_KIND.get(t["kind"])
        if kind is None:
            skipped[f"ledger: noma'lum amal {t['kind']!r}"] += 1
            continue
        transactions.append({
            "id": _u(t["id"]), "kind": kind, "ref_type": t["ref_type"], "ref_id": _u(t["ref_id"]),
            "booking_id": _u(t["booking_id"]), "description": (t["description"] or "")[:255], "created_at": _dt(t["created_at"]),
        })  # fmt: skip
    tx_ids = {t["id"] for t in transactions}
    for e in snap.ledger_entries:
        if _u(e["transaction_id"]) not in tx_ids or e["account"] not in ACCOUNTS:
            skipped["ledger_entries: tranzaksiya/hisob ko'chmadi"] += 1
            continue
        entries.append({
            "transaction_id": _u(e["transaction_id"]), "account": e["account"], "debit_tiyin": tiyin(e["debit"]) or 0,
            "credit_tiyin": tiyin(e["credit"]) or 0, "doctor_id": _u(e["doctor_id"]), "created_at": _dt(e["created_at"]),
        })  # fmt: skip

    discrepancies = [
        {
            "id": _u(d["id"]), "type": d["type"], "provider": d["provider"] or "", "external_id": d["external_id"] or "",
            "payment_id": _u(d["payment_id"]), "booking_id": _u(d["booking_id"]),
            "local_tiyin": tiyin(d["local_amount"]), "provider_tiyin": tiyin(d["provider_amount"]),
            "details": _json(d["details"], {}), "day": _d(d["period_date"]), "status": d["status"],
            "resolution_note": d["resolution_note"] or "", "fingerprint": d["fingerprint"],
            "created_at": _dt(d["created_at"]), "updated_at": _dt(d["updated_at"]),
        }  # fmt: skip
        for d in snap.discrepancies
    ]
    return {
        "payments": payments,
        "refunds": refunds,
        "ledger_transactions": transactions,
        "ledger_entries": entries,
        "discrepancies": discrepancies,
    }


def _entry(kind: str, source_ref: Any, b: dict[str, Any], **values: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid5(NS, f"{kind}:{source_ref}"), "kind": kind, "source_ref": str(source_ref),
        "booking_id": _u(b["id"]), "doctor_id": _u(b["doctor_id"]), "clinic_id": _u(b["clinic_id"]),
        "booking_number": b["number"], "start_at": _dt(b["start_at"]), **values,
    }  # fmt: skip


def plan_billing(snap: Snapshot, skipped: Counter[str], notes: Counter[str]) -> Plan:
    bookings = {_u(b["id"]): b for b in snap.bookings}
    services: dict[uuid.UUID, list[str]] = defaultdict(list)
    for i in sorted(snap.items, key=lambda i: (str(i["created_at"]), str(i["id"]))):
        services[_u(i["booking_id"])].append(i["service_name"])  # type: ignore[index]
    refunds_by_booking: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for r in snap.refunds:
        refunds_by_booking[_u(r["booking_id"])].append(r)  # type: ignore[index]
    paid = {_u(p["booking_id"]) for p in snap.payments if p["status"] in MONEY_TAKEN}

    accounts = [
        {
            "doctor_id": _u(a["doctor_id"]), "holder_name": a["holder_name"], "bank_name": a["bank_name"] or "",
            "account_number": a["account_number"], "mfo": a["mfo"] or "", "inn": a["inn"] or "",
            "updated_at": _dt(a["updated_at"]),
        }  # fmt: skip
        for a in snap.accounts
    ]
    payouts = [
        {
            "id": _u(p["id"]), "doctor_id": _u(p["doctor_id"]), "period_start": _d(p["period_start"]),
            "period_end": _d(p["period_end"]), "status": p["status"], "bookings_count": int(p["bookings_count"] or 0),
            "gross_tiyin": tiyin(p["gross_amount"]) or 0, "platform_fee_tiyin": tiyin(p["platform_fee"]) or 0,
            "provider_fee_tiyin": tiyin(p["provider_fee"]) or 0, "refunds_tiyin": tiyin(p["refunds_amount"]) or 0,
            "net_tiyin": tiyin(p["net_amount"]) or 0, "paid_at": _dt(p["paid_at"]), "paid_by": _u(p["paid_by_id"]),
            "bank_reference": p["bank_reference"] or "", "created_at": _dt(p["created_at"]),
            "updated_at": _dt(p["updated_at"]),
        }  # fmt: skip
        for p in snap.payouts
    ]

    entries: list[dict[str, Any]] = []
    in_payout: set[uuid.UUID] = set()
    for line in snap.payout_lines:
        b = bookings.get(_u(line["booking_id"]))
        if b is None:
            skipped["payout_lines: bron topilmadi"] += 1
            continue
        in_payout.add(_u(b["id"]))  # type: ignore[arg-type]
        entries.append(_entry(
            "earning", _u(b["id"]), b, booking_status=b["status"], services=services.get(_u(b["id"]), []),  # type: ignore[arg-type]
            amount_tiyin=tiyin(line["net_amount"]) or 0, gross_tiyin=tiyin(line["gross_amount"]) or 0,
            platform_tiyin=tiyin(line["platform_fee"]) or 0, provider_tiyin=tiyin(line["provider_fee"]) or 0,
            eligible_at=_dt(b["end_at"]), payout_id=_u(line["payout_id"]),
        ))  # fmt: skip
        # Monolit qator `net` i BARCHA refundlarni (pending ham) allaqachon ayirgan — keyin keladigan
        # `refund.succeeded` ikkinchi marta ayirmasin: 0 summali yozuv (UNIQUE kind+source_ref)
        for r in refunds_by_booking.get(_u(b["id"]), []):  # type: ignore[arg-type]
            entries.append(_entry(
                "refund", _u(r["id"]), b, booking_status=b["status"], services=[], amount_tiyin=0, gross_tiyin=0,
                platform_tiyin=0, provider_tiyin=0, eligible_at=_dt(r["created_at"]), payout_id=_u(line["payout_id"]),
            ))  # fmt: skip

    for b in snap.bookings:
        bid = _u(b["id"])
        eligible = b["status"] == "completed" or (b["status"] == "no_show" and b["no_show_by"] == "client")
        if bid in in_payout or bid not in paid or not eligible:
            continue
        money = money_split(b, notes)
        commission = bp(b["commission_rate"]) or DEFAULT_COMMISSION_BP
        # Faqat yakunlangan refundlar ayiriladi — pending'lari keyin `refund.succeeded` bilan keladi
        done = [r for r in refunds_by_booking.get(bid, []) if r["status"] == "succeeded"]  # type: ignore[arg-type]
        refunded = sum(tiyin(r["amount"]) or 0 for r in done)
        net = max(0, money["doctor_payout_tiyin"] - monolith_doctor_share(refunded, commission))
        notes["earning_entries: payout'ga kirmagan daromad (monolit formulasi)"] += 1
        entries.append(_entry(
            "earning", bid, b, booking_status=b["status"], services=services.get(bid, []), amount_tiyin=net,  # type: ignore[arg-type]
            gross_tiyin=money["total_tiyin"], platform_tiyin=money["platform_fee_tiyin"],
            provider_tiyin=money["provider_fee_tiyin"], eligible_at=_dt(b["completed_at"] or b["end_at"]), payout_id=None,
        ))  # fmt: skip
        for r in done:
            entries.append(_entry(
                "refund", _u(r["id"]), b, booking_status=b["status"], services=[], amount_tiyin=0, gross_tiyin=0,
                platform_tiyin=0, provider_tiyin=0, eligible_at=_dt(r["created_at"]), payout_id=None,
            ))  # fmt: skip
    return {"payout_accounts": accounts, "payouts": payouts, "earning_entries": entries}


KEYS = {"payout_accounts": "doctor_id"}
# Tabiiy kaliti yo'q (identity PK) — faqat SHU ishga tushishda yangi qo'shilgan egasi uchun
DEPENDENT = {"ledger_entries": ("ledger_transactions", "transaction_id")}


def load(engine: Engine, plan: Plan) -> dict[str, int]:
    out: dict[str, int] = {}
    new_owners: dict[str, set[Any]] = {}
    with engine.begin() as conn:
        for table, rows in plan.items():
            if table in DEPENDENT:
                owner, column = DEPENDENT[table]
                rows = [r for r in rows if r[column] in new_owners.get(owner, set())]
            ids = _put(conn, table, rows, KEYS.get(table, "id"))
            new_owners[table] = set(ids)
            out[table] = len(ids)
    return out


def trial_balance(engine: Engine) -> int:
    with engine.connect() as conn:
        value = conn.execute(
            text("SELECT coalesce(sum(debit_tiyin), 0) - coalesce(sum(credit_tiyin), 0) FROM ledger_entries")
        ).scalar_one()
    return int(value)


def run(args: argparse.Namespace) -> int:
    snap = extract(_engine(args.source))
    skipped: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    plans = {"payment": plan_payment(snap, skipped, notes), "billing": plan_billing(snap, skipped, notes)}
    report: dict[str, Any] = {
        "source": {k: len(v) for k, v in vars(snap).items()},
        "plan": {name: {t: len(rows) for t, rows in plan.items()} for name, plan in plans.items()},
        "skipped": dict(sorted(skipped.items())),
        "notes": dict(sorted(notes.items())),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    code = 0
    if not args.dry_run:
        targets = {name: getattr(args, name) for name in plans if getattr(args, name)}
        if not targets:
            print("XATO: hech bir maqsad baza ko'rsatilmagan (--payment/--billing)", file=sys.stderr)
            return 2
        report["inserted"] = {name: load(_engine(url), plans[name]) for name, url in targets.items()}
        report["not_targeted"] = [name for name in plans if name not in targets]
        if "payment" in targets:
            report["ledger_trial_balance"] = balance = trial_balance(_engine(targets["payment"]))
            if balance != 0:
                print(f"XATO: payment daftari balansda emas: {balance}", file=sys.stderr)
                code = 3
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.environ.get("MONOLITH_DATABASE_URL"))
    for name in ("payment", "billing"):
        parser.add_argument(f"--{name}", default=os.environ.get(f"{name.upper()}_DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.source:
        parser.error("--source (yoki MONOLITH_DATABASE_URL) majburiy")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
