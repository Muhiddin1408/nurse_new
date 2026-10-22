"""scripts/migrate/feedback.py — xaritalash qoidalari va haqiqiy Postgres maqsadlarga (review, dispute) ko'chirish.

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
import feedback
from core import _engine

ROOT = Path(__file__).resolve().parents[2]


def _id() -> str:
    return uuid.uuid4().hex


def build() -> tuple[feedback.Snapshot, dict[str, str]]:
    doctor, client = _id(), _id()
    done = {"id": _id(), "number": "MB-1", "client_id": client, "doctor_id": doctor, "status": "completed",
            "completed_at": "2026-10-10 10:00:00", "updated_at": "2026-10-10 10:00:00", "clinic_id": _id()}  # fmt: skip
    other = {**done, "id": _id(), "number": "MB-2", "status": "confirmed", "completed_at": None, "clinic_id": None}
    third = {**other, "id": _id(), "number": "MB-3"}
    refund_ok, refund_pending = _id(), _id()
    snap = feedback.Snapshot(
        bookings=[done, other, third],
        reviews=[{
            "id": _id(), "booking_id": done["id"], "client_id": client, "doctor_id": doctor, "rating": 5,
            "comment": "Zo'r", "is_anonymous": 0, "status": "published", "moderation_flags": "[]",
            "moderated_by_id": None, "moderated_at": None, "doctor_reply": "Rahmat", "doctor_replied_at": "2026-10-11 09:00:00",
            "created_at": "2026-10-10 12:00:00", "updated_at": "2026-10-11 09:00:00", "full_name": "Aziz Tursunov",
        }],  # fmt: skip
        disputes=[
            {"id": _id(), "booking_id": other["id"], "raised_by_id": client, "reason": "poor_service", "description": "",
             "status": "resolved_doctor", "due_at": "2026-10-12 10:00:00", "resolution_note": "eski",
             "resolved_by_id": None, "resolved_at": "2026-10-11 10:00:00", "refund_id": None,
             "created_at": "2026-10-09 10:00:00", "updated_at": "2026-10-11 10:00:00"},
            {"id": _id(), "booking_id": other["id"], "raised_by_id": client, "reason": "doctor_no_show", "description": "kelmadi",
             "status": "resolved_client", "due_at": "2026-10-14 10:00:00", "resolution_note": "ok",
             "resolved_by_id": _id(), "resolved_at": "2026-10-13 10:00:00", "refund_id": refund_ok,
             "created_at": "2026-10-12 10:00:00", "updated_at": "2026-10-13 10:00:00"},
            {"id": _id(), "booking_id": third["id"], "raised_by_id": client, "reason": "weird", "description": "",
             "status": "resolved_client", "due_at": "2026-10-14 10:00:00", "resolution_note": "",
             "resolved_by_id": None, "resolved_at": "2026-10-13 10:00:00", "refund_id": refund_pending,
             "created_at": "2026-10-12 11:00:00", "updated_at": "2026-10-13 10:00:00"},
        ],  # fmt: skip
        payments=[{"booking_id": other["id"], "amount": "150000.00", "status": "refunded"},
                  {"booking_id": third["id"], "amount": "100000.00", "status": "succeeded"}],  # fmt: skip
        refunds=[{"id": refund_ok, "amount": "150000.00", "status": "succeeded", "completed_at": "2026-10-13 11:00:00"},
                 {"id": refund_pending, "amount": "40000.00", "status": "manual_required", "completed_at": None}],  # fmt: skip
    )
    return snap, {"done": done["id"], "other": other["id"], "third": third["id"]}


def test_plan_reviews_and_disputes() -> None:
    snap, ids = build()
    skipped: Counter[str] = Counter()
    review = feedback.plan_review(snap, skipped)
    assert [r["booking_id"].hex for r in review["reviewable_bookings"]] == [ids["done"]]
    r = review["reviews"][0]
    assert r["author_name"] == "Aziz T." and r["clinic_id"] is not None and r["doctor_reply"] == "Rahmat"

    plan = feedback.plan_dispute(snap, skipped, Counter())
    rows = {d["booking_id"].hex: d for d in plan["disputes"]}
    full = rows[ids["other"]]
    assert (full["resolution"], full["status"], full["refunded_tiyin"]) == ("refund_full", "closed", 15_000_000)
    assert full["doctor_liable"] is True and full["paid_tiyin"] == 15_000_000
    partial = rows[ids["third"]]
    assert (partial["resolution"], partial["status"], partial["reason"]) == ("refund_partial", "resolved", "other")
    assert skipped == Counter({"disputes: bronda bir nechta nizo — faqat oxirgisi": 1})
    assert feedback.short_name("Ali") == "Ali" and feedback.short_name(None) == ""


def _alembic(service: str) -> Path:
    return ROOT / "backend" / service / ".venv" / "bin" / "alembic"


@pytest.fixture(scope="module")
def targets() -> Iterator[dict[str, str]]:
    if shutil.which("docker") is None or not all(_alembic(f"{s}-service").exists() for s in ("review", "dispute")):
        pytest.skip("Docker yoki servis .venv (alembic) yo'q")
    password = secrets.token_hex(16)
    name = f"mig-feedback-test-{secrets.token_hex(4)}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={password}",
                    "-p", "127.0.0.1::5432", "postgres:16-alpine"], check=True, capture_output=True)  # fmt: skip
    try:
        port = subprocess.run(["docker", "port", name, "5432"], check=True, capture_output=True, text=True)
        host_port = port.stdout.split(":")[-1].strip()
        for _ in range(60):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres", "-h", "127.0.0.1"],
                              capture_output=True, check=False).returncode == 0:  # fmt: skip
                break
            time.sleep(0.5)
        urls = {}
        for db in ("review", "dispute"):
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
    monkeypatch.setattr(feedback, "extract", lambda engine: snap)
    args = ["--source", "sqlite://", "--review", targets["review"], "--dispute", targets["dispute"]]
    assert feedback.main(args) == 0
    first: dict[str, Any] = json.loads(capsys.readouterr().out)
    assert first["inserted"] == first["plan"]
    assert feedback.main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert all(n == 0 for tables in second["inserted"].values() for n in tables.values())
    with _engine(targets["dispute"]).connect() as conn:
        status = conn.execute(text("SELECT status FROM disputes WHERE booking_id = :b"), {"b": uuid.UUID(ids["other"])})
        assert status.scalar() == "closed"


def test_dry_run_against_dev_sqlite(capsys) -> None:
    db = ROOT / "db.sqlite3"
    if not db.is_file():
        pytest.skip("db.sqlite3 yo'q")
    assert feedback.main(["--dry-run", "--source", f"sqlite:///{db}"]) == 0
    assert set(json.loads(capsys.readouterr().out)["plan"]) == {"review", "dispute"}
