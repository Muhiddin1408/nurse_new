"""scripts/migrate/core.py — xaritalash qoidalari va haqiqiy Postgres maqsadlarga ko'chirish.

    pip install -r scripts/migrate/requirements.txt pytest && pytest scripts/migrate -q
Integratsiya testi Docker (postgres:16-alpine) va servislarning `.venv` (alembic) ini talab qiladi — bo'lmasa skip.
Schedule sxemasi — backend/schedule-service/migrations/*.up.sql (Go servisi, alembic yo'q).
"""

# Test: subprocess — docker/alembic (o'zimiz yaratgan argumentlar), SQL — test sxemasining jadval nomlari
# ruff: noqa: S603, S607, S608

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def uid() -> str:
    return uuid.uuid4().hex  # SQLite'dagi Django UUID ko'rinishi


# --- unit: xaritalash -------------------------------------------------------------------------------------


def test_money_conversion() -> None:
    assert core.tiyin("1234.56") == 123456
    assert core.tiyin(1234.56) == 123456  # SQLite REAL
    assert core.tiyin("0.005") == 1 and core.tiyin("0.004") == 0  # ROUND_HALF_UP
    assert core.tiyin(None) is None
    assert core.bp("0.1500") == 1500 and core.bp(0.025) == 250 and core.bp(None) == 0


def _slot(**kw: Any) -> dict[str, Any]:
    base = {
        "id": uid(),
        "doctor_id": "d" * 32,
        "clinic_id": None,
        "room_id": None,
        "start_at": "2026-10-22 05:00:00",
        "end_at": "2026-10-22 05:30:00",
        "status": "free",
        "hold_expires_at": None,
        "block_reason": "",
        "time_off_id": None,
        "block_note": "",
        "travel_buffer_minutes": None,
        "created_at": "2026-10-01 00:00:00",
        "updated_at": "2026-10-01 00:00:00",
    }
    return {**base, **kw}


def _booking(slot: dict[str, Any], status: str, **kw: Any) -> dict[str, Any]:
    return {"id": uid(), "slot_id": slot["id"], "status": status, "created_at": "2026-10-01 00:00:00", **kw}


def test_slot_states_follow_bookings() -> None:
    confirmed, visited, orphan_booked = _slot(status="booked"), _slot(status="booked"), _slot(status="booked")
    pending, orphan_held = _slot(status="held", hold_expires_at="2026-10-21 08:10:00"), _slot(status="held")
    snap = core.Snapshot(
        slots=[confirmed, visited, orphan_booked, pending, orphan_held],
        bookings=[
            _booking(confirmed, "confirmed"),
            _booking(visited, "cancelled"),
            _booking(visited, "completed"),
            _booking(pending, "pending_payment"),
        ],
    )
    skipped: Counter[str] = Counter()
    slots = {str(s["id"].hex): s for s in core.plan_schedule(snap, skipped, NOW)["slots"]}
    assert orphan_booked["id"] not in slots  # bronsiz 'booked' — tashlanadi va sanaladi
    assert slots[confirmed["id"]]["status"] == "booked" and slots[confirmed["id"]]["ever_held"]
    assert slots[confirmed["id"]]["hold_ref"] == str(uuid.UUID(snap.bookings[0]["id"]))
    assert slots[visited["id"]]["hold_ref"] == str(uuid.UUID(snap.bookings[2]["id"]))  # yakunlangan bron
    assert slots[pending["id"]]["status"] == "held"
    assert slots[pending["id"]]["hold_expires_at"] == datetime(2026, 10, 21, 8, 10, tzinfo=UTC)
    assert slots[orphan_held["id"]]["status"] == "free" and not slots[orphan_held["id"]]["ever_held"]
    assert sum(skipped.values()) == 2 and any("bron yo'q" in k for k in skipped)


def test_travel_buffer_gets_owner_or_is_freed() -> None:
    home = _slot(status="booked", travel_buffer_minutes=30)
    near = _slot(
        status="blocked", block_reason="travel_buffer", start_at="2026-10-22 05:30:00", end_at="2026-10-22 06:00:00"
    )
    far = _slot(
        status="blocked", block_reason="travel_buffer", start_at="2026-10-22 09:00:00", end_at="2026-10-22 09:30:00"
    )
    manual = _slot(
        status="blocked", block_reason="manual", start_at="2026-10-22 10:00:00", end_at="2026-10-22 10:30:00"
    )
    snap = core.Snapshot(slots=[home, near, far, manual], bookings=[_booking(home, "confirmed")])
    skipped: Counter[str] = Counter()
    slots = {s["id"].hex: s for s in core.plan_schedule(snap, skipped, NOW)["slots"]}
    assert slots[near["id"]]["blocked_by_slot_id"] == uuid.UUID(home["id"])  # ADR-0011 #4
    assert slots[far["id"]]["status"] == "free" and slots[far["id"]]["block_reason"] == ""
    assert slots[manual["id"]]["status"] == "blocked" and slots[manual["id"]]["blocked_by_slot_id"] is None
    assert skipped == Counter({"schedule.slots: egasiz travel_buffer bloki → free (qator ko'chdi)": 1})


# --- integratsiya: sqlite manba → haqiqiy Postgres maqsadlar ----------------------------------------------

SOURCE_TABLES: dict[str, list[str]] = {}
for _name, _sql in core.QUERIES.items():
    _cols, _table = _sql.split(" FROM ")
    _table = _table.split()[0]
    if _name == "doctors":
        SOURCE_TABLES["catalog_doctor"] = [
            c.strip().removeprefix("d.") for c in _cols.removeprefix("SELECT ").split(",") if c.strip().startswith("d.")
        ]
        SOURCE_TABLES["account_user"] = ["id", "full_name"]
    else:
        SOURCE_TABLES[_table] = [c.strip() for c in _cols.removeprefix("SELECT ").split(",")]


class Source:
    def __init__(self, path: Path) -> None:
        self.conn = sqlite3.connect(path)
        for table, cols in SOURCE_TABLES.items():
            self.conn.execute(f"CREATE TABLE {table} ({', '.join(cols)})")

    def add(self, table: str, **values: Any) -> dict[str, Any]:
        row = dict.fromkeys(SOURCE_TABLES[table]) | values
        for key in ("created_at", "updated_at"):
            if key in row and row[key] is None:
                row[key] = "2026-09-01 10:00:00"
        row = {k: json.dumps(v) if isinstance(v, list | dict) else v for k, v in row.items()}
        self.conn.execute(
            f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
        )
        return values


def build_source(path: Path) -> dict[str, str]:
    """Monolitga xos holatlar: pending klinika, 0 narxli xizmat, hujjat, egasiz/yetim slotlar, to'lov va refund."""
    db = Source(path)
    ids = {
        k: uid()
        for k in (
            "spec",
            "clinic",
            "draft_clinic",
            "room",
            "user",
            "doctor",
            "service",
            "home_service",
            "package",
            "promo",
            "enrollment",
            "patient",
            "address",
            "client",
        )
    }
    db.add(
        "catalog_specialization",
        id=ids["spec"],
        name="Terapevt",
        name_ru="",
        slug="terapevt",
        is_pediatric=0,
        accepts_children=1,
        allowed_gender="",
    )
    for key, status in (("clinic", "active"), ("draft_clinic", "pending")):
        db.add(
            "catalog_clinic",
            id=ids[key],
            name=f"Klinika {key}",
            description="",
            phone="",
            city="Toshkent",
            street="Amir Temur 1",
            latitude=41.311081,
            longitude=69.240562,
            status=status,
            rating=0,
            reviews_count=0,
            working_hours={"0": ["08:00", "18:00"]},
            photo_url="",
        )
    db.add(
        "catalog_room", id=ids["room"], clinic_id=ids["clinic"], name="101", floor="1", equipment=["UZI"], is_active=1
    )
    db.add("account_user", id=ids["user"], full_name="Dr. Aliyev")
    db.add(
        "catalog_doctor",
        id=ids["doctor"],
        user_id=ids["user"],
        bio="",
        experience_years=7,
        education="",
        languages=["uz", "ru"],
        license_number="L-1",
        license_expires_at="2027-12-31",
        status="approved",
        clinic_payment_mode="at_clinic",
        follow_up_discount_percent=10,
        accepts_home_visits=1,
        home_visit_radius_km=15,
        home_base_latitude="41.300000",
        home_base_longitude="69.200000",
        rating="4.50",
        reviews_count=4,
        status_reason="",
    )
    db.add("catalog_doctor_specializations", doctor_id=ids["doctor"], specialization_id=ids["spec"])
    db.add("catalog_doctordocument", id=uid(), doctor_id=ids["doctor"], kind="diploma")
    db.add("catalog_clinicmembership", id=uid(), user_id=uid(), clinic_id=ids["clinic"], is_active=1)
    db.add(
        "catalog_doctoraffiliation",
        id=uid(),
        doctor_id=ids["doctor"],
        clinic_id=ids["clinic"],
        position="",
        status="active",
    )
    db.add(
        "catalog_doctoraffiliation",
        id=uid(),
        doctor_id=ids["doctor"],
        clinic_id=ids["draft_clinic"],
        position="",
        status="invited",
    )
    svc = {
        "specialization_id": ids["spec"],
        "name_ru": "",
        "description": "",
        "is_active": 1,
        "mxik_code": "",
        "package_code": "",
        "vat_percent": None,
    }
    db.add(
        "catalog_service",
        id=ids["service"],
        clinic_id=ids["clinic"],
        doctor_id=ids["doctor"],
        name="Konsultatsiya",
        place="clinic",
        duration_minutes=30,
        price="150000.00",
        **svc,
    )
    db.add(
        "catalog_service",
        id=ids["home_service"],
        doctor_id=ids["doctor"],
        name="Uyga chaqiruv",
        place="home",
        duration_minutes=60,
        price=250000.5,
        **svc,
    )
    db.add(
        "catalog_service",
        id=uid(),
        doctor_id=ids["doctor"],
        name="Bepul",
        place="clinic",
        duration_minutes=15,
        price="0",
        **svc,
    )
    db.add(
        "catalog_servicepricehistory",
        service_id=ids["service"],
        old_price="120000",
        new_price="150000",
        is_large_change=0,
        created_at="2026-09-02 10:00:00",
    )
    db.add(
        "catalog_pricerule",
        id=uid(),
        doctor_id=ids["doctor"],
        name="Ertalab",
        weekdays="01234",
        start_time="08:00:00",
        end_time="10:00:00",
        percent=-15,
        priority=100,
        is_active=1,
    )
    db.add(
        "catalog_servicepackage",
        id=ids["package"],
        doctor_id=ids["doctor"],
        service_id=ids["service"],
        name="5 qabul",
        sessions=5,
        discount_percent=10,
        validity_days=90,
        is_active=1,
    )
    db.add(
        "schedule_workingrule",
        id=uid(),
        doctor_id=ids["doctor"],
        clinic_id=ids["clinic"],
        room_id=ids["room"],
        weekday=0,
        start_time="09:00:00",
        end_time="17:00:00",
        slot_minutes=30,
        valid_from="2026-09-01",
    )
    db.add("schedule_scheduleexception", id=uid(), doctor_id=ids["doctor"], date="2026-11-04", reason="Bayram")
    db.add("schedule_clinicclosure", id=uid(), clinic_id=ids["clinic"], date="2026-12-31", reason="")
    time_off = uid()
    db.add(
        "schedule_timeoff",
        id=time_off,
        doctor_id=ids["doctor"],
        start_at="2026-11-10 04:00:00",
        end_at="2026-11-10 06:00:00",
        kind="vacation",
        reason="",
    )

    def slot(start: str, minutes: int = 30, **kw: Any) -> str:
        sid = uid()
        begin = datetime.fromisoformat(start)
        db.add(
            "schedule_timeslot",
            id=sid,
            doctor_id=ids["doctor"],
            start_at=start,
            end_at=str(begin + timedelta(minutes=minutes)),
            **({"block_reason": "", "block_note": ""} | kw),
        )
        return sid

    clinic_kw = {"clinic_id": ids["clinic"], "room_id": None}
    ids["slot_confirmed"] = slot("2026-11-02 04:00:00", status="booked", **clinic_kw)
    ids["slot_done"] = slot("2026-10-05 04:00:00", status="booked", **clinic_kw)
    slot("2026-10-05 05:00:00", status="booked", **clinic_kw)  # bronsiz — tashlanadi
    ids["slot_home"] = slot("2026-11-03 06:00:00", 60, status="booked", travel_buffer_minutes=30)
    ids["slot_buffer"] = slot("2026-11-03 07:00:00", status="blocked", block_reason="travel_buffer")
    slot("2026-11-05 07:00:00", status="blocked", block_reason="travel_buffer")  # yetim
    slot("2026-11-10 04:00:00", status="blocked", block_reason="time_off", time_off_id=time_off)
    slot("2026-11-06 04:00:00", status="free", **clinic_kw)

    db.add(
        "account_patient",
        id=ids["patient"],
        full_name="Bemor",
        relation="O'zim",
        birth_date="1990-05-01",
        gender="male",
        weight_kg=80,
    )
    db.add(
        "account_address",
        id=ids["address"],
        label="Uy",
        city="Toshkent",
        district="Chilonzor",
        street="Bunyodkor 5",
        entrance="2",
        floor="3",
        apartment="12",
        comment="",
        latitude="41.285000",
        longitude="69.204000",
    )
    money = {
        "discount_kind": "",
        "promo_code": "",
        "price_adjust_percent": 0,
        "discount_borne_by": "",
        "client_comment": "",
        "cancelled_reason": "",
        "completed_by": "",
        "no_show_by": "",
        "note": "",
        "rescheduled_count": 0,
        "platform_fee": "0",
        "provider_fee": "0",
        "doctor_payout": "0",
    }

    def booking(key: str, slot_key: str, status: str, **kw: Any) -> None:
        ids[key] = uid()
        db.add(
            "booking_booking",
            id=ids[key],
            number=f"MB-{key[:12]}",
            client_id=ids["client"],
            patient_id=ids["patient"],
            doctor_id=ids["doctor"],
            slot_id=ids[slot_key],
            **({**money, **kw}),
            status=status,
        )
        db.add(
            "booking_bookingitem",
            booking_id=ids[key],
            service_id=ids["service"],
            service_name="Konsultatsiya",
            price=kw.get("total_price", "150000"),
            duration_minutes=30,
            mxik_code="",
            package_code="",
            vat_percent=0,
        )

    booking(
        "b_confirmed",
        "slot_confirmed",
        "confirmed",
        payment_mode="at_clinic",
        total_price="150000",
        original_price=None,
        discount_amount="0",
        prepay_amount="0",
        commission_rate="0.1500",
        provider_fee_rate="0",
        started_at="2026-11-02 04:01:00",
    )
    booking(
        "b_done",
        "slot_done",
        "completed",
        payment_mode="prepaid",
        total_price="135000.00",
        original_price="150000.00",
        discount_amount="15000.00",
        discount_kind="promo",
        promo_code="KUZ10",
        prepay_amount="135000.00",
        commission_rate="0.1500",
        provider_fee_rate="0.0200",
        platform_fee="5250.00",
        provider_fee="2700.00",
        doctor_payout="127050.00",
        completed_at="2026-10-05 05:00:00",
        completed_by="doctor",
    )
    booking(
        "b_home",
        "slot_home",
        "confirmed",
        payment_mode="prepaid",
        total_price="250000.50",
        original_price="250000.50",
        discount_amount="0",
        prepay_amount="250000.50",
        commission_rate="0.1",
        provider_fee_rate="0",
        address_id=ids["address"],
    )
    db.add(
        "payment_payment",
        id=uid(),
        booking_id=ids["b_done"],
        amount="135000.00",
        status="partially_refunded",
        updated_at="2026-10-04 10:00:00",
    )
    db.add(
        "payment_payment",
        id=uid(),
        booking_id=ids["b_done"],
        amount="135000.00",
        status="failed",
        updated_at="2026-10-04 09:00:00",
    )
    db.add("payment_refund", booking_id=ids["b_done"], amount="10000.00", status="succeeded")
    db.add(
        "booking_waitlistentry",
        id=uid(),
        client_id=ids["client"],
        doctor_id=ids["doctor"],
        date_from="2026-11-01",
        date_to="2026-11-07",
        place="clinic",
        status="active",
        notified_count=0,
    )
    db.add(
        "booking_promocode",
        id=ids["promo"],
        code="kuz10",
        kind="percent",
        value="10.00",
        max_discount="50000",
        min_amount="0",
        max_uses=100,
        max_uses_per_user=1,
        first_booking_only=0,
        borne_by="platform",
        is_active=1,
    )
    db.add(
        "booking_promoredemption",
        id=uid(),
        promo_id=ids["promo"],
        booking_id=ids["b_done"],
        client_id=ids["client"],
        amount="15000.00",
        is_active=1,
    )
    db.add(
        "booking_packageenrollment",
        id=ids["enrollment"],
        package_id=ids["package"],
        client_id=ids["client"],
        doctor_id=ids["doctor"],
        service_id=ids["service"],
        sessions_total=5,
        discount_percent=10,
        expires_at="2027-01-01 00:00:00",
        is_active=1,
    )
    db.add(
        "booking_packageuse",
        id=uid(),
        enrollment_id=ids["enrollment"],
        booking_id=ids["b_confirmed"],
        amount="15000.00",
        is_active=0,
    )
    db.conn.commit()
    db.conn.close()
    return ids


def _venv_alembic(service: str) -> Path:
    return ROOT / "backend" / service / ".venv" / "bin" / "alembic"


@pytest.fixture(scope="module")
def targets() -> dict[str, str]:
    if shutil.which("docker") is None or not all(
        _venv_alembic(f"{s}-service").exists() for s in ("catalog", "pricing", "booking")
    ):
        pytest.skip("Docker yoki servis .venv (alembic) yo'q")
    password = secrets.token_hex(16)  # faqat shu test konteyneri uchun, hech qayerga yozilmaydi
    name = f"mig-core-test-{secrets.token_hex(4)}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-p",
            "127.0.0.1::5432",
            "postgres:16-alpine",
        ],
        check=True,
        capture_output=True,
    )
    try:
        port = subprocess.run(["docker", "port", name, "5432"], check=True, capture_output=True, text=True)
        host_port = port.stdout.split(":")[-1].strip()
        for _ in range(60):
            ready = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "postgres", "-h", "127.0.0.1"],
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        urls = {}
        for db in ("catalog", "schedule", "pricing", "booking"):
            subprocess.run(
                ["docker", "exec", name, "psql", "-U", "postgres", "-qc", f"CREATE DATABASE {db}"], check=True
            )
            urls[db] = f"postgresql://postgres:{password}@127.0.0.1:{host_port}/{db}"
        for db in ("catalog", "pricing", "booking"):
            env = {
                **os.environ,
                "MEDBRON_ENV_FILE": "",
                "DATABASE_URL": urls[db].replace("postgresql://", "postgresql+asyncpg://"),
            }
            subprocess.run(
                [str(_venv_alembic(f"{db}-service")), "upgrade", "head"],
                cwd=ROOT / "backend" / f"{db}-service",
                env=env,
                check=True,
                capture_output=True,
            )
        with core._engine(urls["schedule"]).begin() as conn:
            for sql in sorted((ROOT / "backend/schedule-service/migrations").glob("*.up.sql")):
                conn.exec_driver_sql(sql.read_text())
        yield urls
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def _migrate(source: Path, urls: dict[str, str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    args = ["--source", f"sqlite:///{source}"]
    for name, url in urls.items():
        args += [f"--{name}", url]
    assert core.main(args) == 0
    return json.loads(capsys.readouterr().out)


def test_migrates_into_service_schemas_idempotently(tmp_path: Path, targets: dict[str, str], capsys) -> None:
    source = tmp_path / "monolith.sqlite3"
    ids = build_source(source)
    first = _migrate(source, targets, capsys)
    assert first["inserted"] == first["plan"]  # rejadagi hamma qator yozildi
    assert first["plan"]["catalog"]["services"] == 2 and first["plan"]["schedule"]["slots"] == 7
    assert first["plan"]["schedule"]["affiliations"] == 1  # invited — proyeksiyaga kirmaydi
    skipped = first["skipped"]
    assert any("narx 0" in k for k in skipped) and any("diploma" in k for k in skipped)
    assert any("pending → draft" in k for k in skipped) and any("bron yo'q" in k for k in skipped)
    assert any("travel_buffer" in k for k in skipped)

    second = _migrate(source, targets, capsys)  # idempotent: hech narsa takrorlanmaydi
    assert all(n == 0 for tables in second["inserted"].values() for n in tables.values())

    u = uuid.UUID
    with create_engine(targets["booking"].replace("postgresql://", "postgresql+psycopg://")).connect() as conn:
        done = conn.execute(text("SELECT * FROM bookings WHERE id = :id"), {"id": u(ids["b_done"])}).mappings().one()
        assert (done["total_tiyin"], done["original_tiyin"], done["discount_tiyin"]) == (
            13_500_000,
            15_000_000,
            1_500_000,
        )
        assert (done["commission_bp"], done["provider_fee_bp"], done["doctor_payout_tiyin"]) == (1500, 200, 12_705_000)
        assert (done["paid_tiyin"], done["refunded_tiyin"]) == (13_500_000, 1_000_000)
        assert done["patient"]["birth_date"] == "1990-05-01" and done["patient"]["weight_kg"] == 80
        assert done["place"] == "clinic" and done["address"] is None
        home = conn.execute(text("SELECT * FROM bookings WHERE id = :id"), {"id": u(ids["b_home"])}).mappings().one()
        assert home["total_tiyin"] == 25_000_050 and home["place"] == "home" and home["address"]["floor"] == "3"
        status = conn.execute(
            text("SELECT status FROM bookings WHERE id = :id"), {"id": u(ids["b_confirmed"])}
        ).scalar()
        assert status == "in_progress"  # monolitda confirmed + started_at
        assert (
            conn.execute(text("SELECT count(*) FROM booking_items")).scalar() == 3
        )  # ikkinchi ishga tushish qo'shmadi
    with create_engine(targets["schedule"].replace("postgresql://", "postgresql+psycopg://")).connect() as conn:
        buffer = conn.execute(
            text("SELECT blocked_by_slot_id FROM slots WHERE id = :id"), {"id": u(ids["slot_buffer"])}
        )
        assert buffer.scalar() == u(ids["slot_home"])
        ref = conn.execute(text("SELECT hold_ref FROM slots WHERE id = :id"), {"id": u(ids["slot_confirmed"])}).scalar()
        assert ref == str(u(ids["b_confirmed"]))
    with create_engine(targets["pricing"].replace("postgresql://", "postgresql+psycopg://")).connect() as conn:
        promo = conn.execute(text("SELECT code, value, max_discount_tiyin FROM promo_codes")).one()
        assert tuple(promo) == ("KUZ10", 10, 5_000_000)
        assert conn.execute(text("SELECT state FROM package_uses")).scalar() == "released"
        assert (
            conn.execute(
                text("SELECT price_tiyin FROM services_cache WHERE service_id = :id"), {"id": u(ids["home_service"])}
            ).scalar()
            == 25_000_050
        )
    with create_engine(targets["catalog"].replace("postgresql://", "postgresql+psycopg://")).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM service_price_history")).scalar() == 1
        assert (
            conn.execute(text("SELECT status FROM clinics WHERE id = :id"), {"id": u(ids["draft_clinic"])}).scalar()
            == "draft"
        )
        doctor = conn.execute(text("SELECT full_name, languages FROM doctors")).one()
        assert doctor.full_name == "Dr. Aliyev" and doctor.languages == ["uz", "ru"]


def test_dry_run_against_dev_sqlite(capsys) -> None:
    """Haqiqiy monolit sxemasi (db.sqlite3) bo'lsa — barcha so'rovlar o'qiy olishini tekshiradi."""
    db = ROOT / "db.sqlite3"
    if not db.is_file():
        pytest.skip("db.sqlite3 yo'q")
    assert core.main(["--dry-run", "--source", f"sqlite:///{db}"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["plan"]["booking"]["bookings"] == out["source"]["bookings"] - sum(
        n for k, n in out["skipped"].items() if k.startswith("booking.bookings") and "qator ko'chdi" not in k
    )
