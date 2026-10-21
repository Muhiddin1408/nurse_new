"""scripts/migrate/money.py — xaritalash qoidalari va haqiqiy Postgres maqsadlarga (payment, billing) ko'chirish.

    pip install -r scripts/migrate/requirements.txt pytest && pytest scripts/migrate -q
Integratsiya testi Docker (postgres:16-alpine) va servislarning `.venv` (alembic) ini talab qiladi — bo'lmasa skip.
"""

# ruff: noqa: S603, S607, S608

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import money
from core import _engine

ROOT = Path(__file__).resolve().parents[2]
DOCTOR = uuid.uuid4().hex


def _booking(**kw: Any) -> dict[str, Any]:
    base = {
        "id": uuid.uuid4().hex, "number": f"MB-{secrets.token_hex(3)}", "status": "completed",
        "client_id": uuid.uuid4().hex, "doctor_id": DOCTOR, "total_price": "150000.00", "platform_fee": "22500.00",
        "provider_fee": "1500.00", "doctor_payout": "126000.00", "commission_rate": "0.1500", "discount_amount": "0",
        "discount_borne_by": "", "no_show_by": "", "completed_at": "2026-10-10 10:00:00", "clinic_id": None,
        "start_at": "2026-10-10 08:00:00", "end_at": "2026-10-10 09:00:00",
    }  # fmt: skip
    return {**base, **kw}


def _payment(b: dict[str, Any], **kw: Any) -> dict[str, Any]:
    base = {
        "id": uuid.uuid4().hex, "booking_id": b["id"], "provider": "payme", "amount": b["total_price"],
        "status": "succeeded", "external_id": secrets.token_hex(8), "provider_create_time": 1, "perform_time": 2,
        "cancel_time": None, "cancel_reason": None, "provider_state": "{}", "needs_refund": 0,
        "created_at": "2026-10-09 10:00:00", "updated_at": "2026-10-09 10:01:00",
    }  # fmt: skip
    return {**base, **kw}


def _refund(p: dict[str, Any], amount: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": uuid.uuid4().hex, "payment_id": p["id"], "booking_id": p["booking_id"], "amount": amount,
        "reason": "client_cancelled", "status": "succeeded", "initiated_by": "client", "external_refund_id": "",
        "attempts": 0, "next_attempt_at": None, "last_error": "", "completed_at": "2026-10-11 10:00:00",
        "created_at": "2026-10-11 09:00:00", "updated_at": "2026-10-11 10:00:00",
    }  # fmt: skip
    return {**base, **kw}


def _ledger(kind: str, ref: str, booking: str | None, lines: list[tuple[str, str, str]]) -> tuple[dict, list[dict]]:
    tid = uuid.uuid4().hex
    txn = {"id": tid, "kind": kind, "ref_type": kind.split("_")[0], "ref_id": ref, "booking_id": booking,
           "description": kind, "created_at": "2026-10-09 10:01:00"}  # fmt: skip
    entries = [
        {"transaction_id": tid, "account": a, "debit": d, "credit": c, "doctor_id": DOCTOR if a == "doctor_payable" else None,
         "created_at": "2026-10-09 10:01:00"}  # fmt: skip
        for a, d, c in lines
    ]
    return txn, entries


def build() -> tuple[money.Snapshot, dict[str, Any]]:
    """Ssenariylar: (1) yakunlangan, payout'ga kirgan (pending refund bilan); (2) yakunlangan, payout'siz,
    qisman refund; (3) Payme kabinetidan qaytarilgan; (4) ochiq urinish; (5) B8 dan oldingi bron."""
    paid_out = _booking()
    unpaid = _booking(completed_at="2026-10-17 10:00:00")
    reversed_ = _booking(status="cancelled")
    open_ = _booking(status="pending_payment", completed_at=None)
    legacy = _booking(platform_fee="0", provider_fee="0", doctor_payout="0", total_price="100000.00")
    p1, p2 = _payment(paid_out), _payment(unpaid, provider="click", provider_state=json.dumps(
        {"click_paydoc_id": "77", "fiscal": {"submitted": True, "at": "2026-10-09T10:05:00+00:00"}}))  # fmt: skip
    p3 = _payment(reversed_, status="refunded", cancel_time=5, cancel_reason=5)
    p4 = _payment(open_, status="processing", perform_time=None)
    p5 = _payment(legacy)
    r1 = _refund(p1, "10000.00", status="manual_required", completed_at=None)
    r2 = _refund(p2, "50000.00")
    r3 = _refund(p3, "150000.00", reason="provider_cancelled", initiated_by="system")
    ledger = [
        _ledger("payment_succeeded", p1["id"], paid_out["id"],
                [("client_payments", "150000.00", "0"), ("doctor_payable", "0", "126000.00"),
                 ("platform_revenue", "0", "22500.00"), ("provider_fees", "0", "1500.00")]),
        _ledger("refund_succeeded", r2["id"], unpaid["id"],
                [("platform_revenue", "7500.00", "0"), ("doctor_payable", "42500.00", "0"),
                 ("client_payments", "0", "50000.00")]),
    ]  # fmt: skip
    payout_id = uuid.uuid4().hex
    snap = money.Snapshot(
        payments=[p1, p2, p3, p4, p5],
        refunds=[r1, r2, r3],
        ledger_transactions=[t for t, _ in ledger],
        ledger_entries=[e for _, es in ledger for e in es],
        discrepancies=[{
            "id": uuid.uuid4().hex, "type": "ledger_imbalance", "provider": "", "external_id": "", "payment_id": None,
            "booking_id": None, "local_amount": None, "provider_amount": None, "details": '{"imbalance": "1.00"}',
            "period_date": "2026-10-01", "status": "resolved", "resolution_note": "ok", "fingerprint": "ledger_imbalance::::",
            "created_at": "2026-10-02 03:00:00", "updated_at": "2026-10-02 09:00:00",
        }],  # fmt: skip
        accounts=[{"doctor_id": DOCTOR, "holder_name": "Aliyev A.", "bank_name": "Kapital", "account_number": "8600" * 4,
                   "mfo": "00440", "inn": "", "updated_at": "2026-10-01 00:00:00"}],  # fmt: skip
        payouts=[{
            "id": payout_id, "doctor_id": DOCTOR, "period_start": "2026-10-06", "period_end": "2026-10-12",
            "status": "pending", "bookings_count": 1, "gross_amount": "150000.00", "platform_fee": "22500.00",
            "provider_fee": "1500.00", "refunds_amount": "10000.00", "net_amount": "117500.00", "paid_at": None,
            "paid_by_id": None, "bank_reference": "", "created_at": "2026-10-13 04:00:00", "updated_at": "2026-10-13 04:00:00",
        }],  # fmt: skip
        payout_lines=[{"payout_id": payout_id, "booking_id": paid_out["id"], "gross_amount": "150000.00",
                       "platform_fee": "22500.00", "provider_fee": "1500.00", "refunds_amount": "10000.00",
                       "net_amount": "117500.00"}],  # fmt: skip
        bookings=[paid_out, unpaid, reversed_, open_, legacy],
        items=[
            {"booking_id": open_["id"], "service_name": "A", "price": "100000.00", "mxik_code": "M", "package_code": "P",
             "vat_percent": 0, "created_at": "1", "id": "1"},
            {"booking_id": open_["id"], "service_name": "B", "price": "50000.00", "mxik_code": "M", "package_code": "P",
             "vat_percent": 12, "created_at": "2", "id": "2"},
        ],  # fmt: skip
    )
    return snap, {"paid_out": paid_out, "unpaid": unpaid, "reversed": reversed_, "open": open_, "legacy": legacy,
                  "p1": p1, "p2": p2, "p3": p3, "p4": p4, "r1": r1, "r2": r2, "r3": r3, "payout": payout_id}  # fmt: skip


# --- unit -----------------------------------------------------------------------------------------------------


def test_plan_maps_payments_refunds_and_ledger() -> None:
    snap, ids = build()
    skipped: Counter[str] = Counter()
    notes: Counter[str] = Counter()
    plan = money.plan_payment(snap, skipped, notes)
    by_id = {p["id"].hex: p for p in plan["payments"]}
    assert by_id[ids["p3"]["id"]]["status"] == "reversed"  # Payme kabinetidan qaytarilgan
    assert by_id[ids["p2"]["id"]]["fiscal_submitted_at"] is not None
    open_payment = by_id[ids["p4"]["id"]]
    assert open_payment["captured_at"] is None and open_payment["amount_tiyin"] == 15_000_000
    assert sum(i["price_tiyin"] for i in open_payment["receipt_items"]) == 15_000_000
    legacy = by_id[snap.payments[4]["id"]]["money"]
    assert legacy["platform_fee_tiyin"] + legacy["provider_fee_tiyin"] + legacy["doctor_payout_tiyin"] == 10_000_000
    assert any("standart stavkalar" in k for k in notes)

    refunds = {r["id"].hex: r for r in plan["refunds"]}
    assert refunds[ids["r2"]["id"]]["doctor_share_tiyin"] == 4_250_000  # monolit: R − R×15% (ledger bilan mos)
    assert refunds[ids["r3"]["id"]]["source"] == "admin" and refunds[ids["r1"]["id"]]["status"] == "manual_required"
    assert {t["kind"] for t in plan["ledger_transactions"]} == {"payment_captured", "refund_succeeded"}
    assert sum(e["debit_tiyin"] - e["credit_tiyin"] for e in plan["ledger_entries"]) == 0
    assert plan["discrepancies"][0]["day"].isoformat() == "2026-10-01"
    assert not skipped


def test_plan_billing_keeps_monolith_net_and_blocks_double_deduction() -> None:
    snap, ids = build()
    plan = money.plan_billing(snap, Counter(), Counter())
    entries = {(e["kind"], e["source_ref"].replace("-", "")): e for e in plan["earning_entries"]}
    line = entries[("earning", ids["paid_out"]["id"])]
    assert line["amount_tiyin"] == 11_750_000 and line["payout_id"].hex == ids["payout"]
    # payout'dagi bronning pending refundi — 0 summali "tutqich": keyingi refund.succeeded ikkinchi marta ayirmaydi
    assert entries[("refund", ids["r1"]["id"])]["amount_tiyin"] == 0
    unpaid = entries[("earning", ids["unpaid"]["id"])]
    assert unpaid["amount_tiyin"] == 12_600_000 - 4_250_000 and unpaid["payout_id"] is None
    assert ("earning", ids["reversed"]["id"]) not in entries and ("earning", ids["open"]["id"]) not in entries
    assert plan["payouts"][0]["net_tiyin"] == 11_750_000 and plan["payout_accounts"][0]["account_number"] == "8600" * 4


# --- integratsiya: haqiqiy servis sxemalari --------------------------------------------------------------------


def _alembic(service: str) -> Path:
    return ROOT / "backend" / service / ".venv" / "bin" / "alembic"


@pytest.fixture(scope="module")
def targets() -> Iterator[dict[str, str]]:
    if shutil.which("docker") is None or not all(_alembic(f"{s}-service").exists() for s in ("payment", "billing")):
        pytest.skip("Docker yoki servis .venv (alembic) yo'q")
    password = secrets.token_hex(16)
    name = f"mig-money-test-{secrets.token_hex(4)}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={password}",
                    "-p", "127.0.0.1::5432", "postgres:16-alpine"], check=True, capture_output=True)  # fmt: skip
    try:
        port = subprocess.run(["docker", "port", name, "5432"], check=True, capture_output=True, text=True)
        host_port = port.stdout.split(":")[-1].strip()
        for _ in range(60):
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres", "-h", "127.0.0.1"],
                                   capture_output=True, check=False)  # fmt: skip
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        urls = {}
        for db in ("payment", "billing"):
            subprocess.run(["docker", "exec", name, "psql", "-U", "postgres", "-qc", f"CREATE DATABASE {db}"], check=True)
            urls[db] = f"postgresql://postgres:{password}@127.0.0.1:{host_port}/{db}"
            env = {**os.environ, "MEDBRON_ENV_FILE": "",
                   "DATABASE_URL": urls[db].replace("postgresql://", "postgresql+asyncpg://")}  # fmt: skip
            subprocess.run([str(_alembic(f"{db}-service")), "upgrade", "head"], cwd=ROOT / "backend" / f"{db}-service",
                           env=env, check=True, capture_output=True)  # fmt: skip
        yield urls
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def test_loads_into_service_schemas_idempotently(targets: dict[str, str], monkeypatch, capsys) -> None:
    snap, ids = build()
    monkeypatch.setattr(money, "extract", lambda engine: snap)
    args = ["--source", "sqlite://", "--payment", targets["payment"], "--billing", targets["billing"]]
    assert money.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["inserted"] == first["plan"] and first["ledger_trial_balance"] == 0
    assert money.main(args) == 0  # ikkinchi marta — hech narsa takrorlanmaydi
    second = json.loads(capsys.readouterr().out)
    assert all(n == 0 for tables in second["inserted"].values() for n in tables.values())

    with _engine(targets["payment"]).connect() as conn:
        status = conn.execute(text("SELECT status FROM payments WHERE id = :id"), {"id": uuid.UUID(ids["p3"]["id"])})
        assert status.scalar() == "reversed"
        assert conn.execute(text("SELECT count(*) FROM ledger_entries")).scalar() == 7
    with _engine(targets["billing"]).connect() as conn:
        unpaid = conn.execute(
            text("SELECT amount_tiyin FROM earning_entries WHERE kind = 'earning' AND booking_id = :id"),
            {"id": uuid.UUID(ids["unpaid"]["id"])},
        ).scalar()
        assert unpaid == 12_600_000 - 4_250_000
        # payout'ga kirmaganlar: qisman refundli bron + B8 dan oldingi bron (standart stavkalar: 84%)
        net = conn.execute(text("SELECT sum(amount_tiyin) FROM earning_entries WHERE payout_id IS NULL")).scalar()
        assert net == 8_350_000 + 8_400_000


def test_dry_run_against_dev_sqlite(capsys) -> None:
    """Haqiqiy monolit sxemasi (db.sqlite3) bo'lsa — barcha so'rovlar o'qiy olishini tekshiradi."""
    db = ROOT / "db.sqlite3"
    if not db.is_file():
        pytest.skip("db.sqlite3 yo'q")
    assert money.main(["--dry-run", "--source", f"sqlite:///{db}"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["plan"]) == {"payment", "billing"}
