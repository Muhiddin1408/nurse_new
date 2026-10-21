"""catalog / user-service eventlari → rollar proyeksiyasi va akkaunt o'chirish. Docker talab qiladi."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from medbron_common.eventbus import PermanentError
from medbron_common.events import CATALOG
from medbron_common.testing import assert_event_valid

from app.services.projections import ProjectionNotReady

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def create_user(db, phone: str = "+998901234567") -> str:
    uid = str(uuid.uuid4())
    db.scalar("INSERT INTO users (id, phone, is_phone_verified) VALUES (:id, :phone, true)", id=uid, phone=phone)
    return uid


def roles_events(db) -> list[dict]:
    rows = db.all("SELECT payload FROM outbox_events WHERE event_type = 'user.roles_changed' ORDER BY id")
    for (payload,) in rows:
        assert_event_valid(payload)
    return [row[0]["data"] for row in rows]


def doctor_event(uid: str, doctor_id: uuid.UUID, **extra: object) -> dict:
    return {"doctor_id": str(doctor_id), "user_id": uid, **extra}


def approved(uid: str, doctor_id: uuid.UUID) -> dict:
    return doctor_event(
        uid,
        doctor_id,
        full_name="Dr",
        specialization_ids=[],
        accepts_home_visits=True,
        approved_by=str(uuid.uuid4()),
        approved_at="2026-10-21T08:00:00Z",
    )


def test_every_catalog_consume_has_handler(run_handler) -> None:
    from medbron_common.eventbus import EventConsumer

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="auth-service", amqp_url="amqp://x/", session_factory=None, processed_model=ProcessedEvent
    )
    handlers.register(consumer)
    expected = sorted(e for e, spec in CATALOG.items() if "auth-service" in spec.consumers)
    assert consumer.routing_keys == expected


def test_doctor_lifecycle_keeps_role_on_suspend_and_ignores_stale_events(db, run_handler) -> None:
    uid = create_user(db)
    doctor_id = uuid.uuid4()
    run_handler("doctor.approved", approved(uid, doctor_id), occurred_at=T0)
    assert roles_events(db) == [{"user_id": uid, "roles": ["client", "doctor"], "added": ["doctor"], "removed": []}]

    run_handler(
        "doctor.suspended",
        doctor_event(uid, doctor_id, reason="hujjat", suspended_by=str(uuid.uuid4())),
        occurred_at=T0 + timedelta(minutes=5),
    )
    assert db.scalar("SELECT status FROM doctor_links WHERE user_id = :u", u=uid) == "suspended"
    assert len(roles_events(db)) == 1  # rol saqlanadi

    # Retry tufayli kechikib kelgan ESKI approved — yangi holatni buzmaydi
    run_handler("doctor.approved", approved(uid, doctor_id), occurred_at=T0 + timedelta(minutes=1))
    assert db.scalar("SELECT status FROM doctor_links WHERE user_id = :u", u=uid) == "suspended"

    run_handler(
        "doctor.reinstated",
        doctor_event(uid, doctor_id, reinstated_by=str(uuid.uuid4())),
        occurred_at=T0 + timedelta(minutes=10),
    )
    assert db.scalar("SELECT status FROM doctor_links WHERE user_id = :u", u=uid) == "approved"


def test_suspend_before_approve_is_retried(db, run_handler) -> None:
    uid = create_user(db)
    with pytest.raises(ProjectionNotReady):
        run_handler("doctor.suspended", doctor_event(uid, uuid.uuid4(), reason="x", suspended_by=str(uuid.uuid4())))


def test_unknown_user_goes_to_dlq(db, run_handler) -> None:
    with pytest.raises(PermanentError):
        run_handler("doctor.approved", approved(str(uuid.uuid4()), uuid.uuid4()))


def test_clinic_membership_roles(db, run_handler) -> None:
    uid = create_user(db)
    c1, c2 = str(uuid.uuid4()), str(uuid.uuid4())
    run_handler("clinic.member_added", {"clinic_id": c1, "user_id": uid}, occurred_at=T0)
    run_handler("clinic.member_added", {"clinic_id": c2, "user_id": uid}, occurred_at=T0 + timedelta(seconds=1))
    run_handler(
        "clinic.member_removed",
        {"clinic_id": c1, "user_id": uid, "remaining_clinics": 1},
        occurred_at=T0 + timedelta(seconds=2),
    )
    # Tartibsiz kelgan eski "added" — tombstone'ni tiriltirmaydi
    run_handler("clinic.member_added", {"clinic_id": c1, "user_id": uid}, occurred_at=T0)
    assert db.scalar("SELECT is_active FROM clinic_memberships WHERE clinic_id = :c", c=c1) is False
    run_handler(
        "clinic.member_removed",
        {"clinic_id": c2, "user_id": uid, "remaining_clinics": 0},
        occurred_at=T0 + timedelta(seconds=3),
    )
    assert roles_events(db) == [
        {"user_id": uid, "roles": ["client", "clinic_admin"], "added": ["clinic_admin"], "removed": []},
        {"user_id": uid, "roles": ["client"], "added": [], "removed": ["clinic_admin"]},
    ]


def test_deletion_frees_phone_and_revokes_sessions(db, run_handler) -> None:
    uid = create_user(db)
    db.scalar(
        "INSERT INTO refresh_tokens (user_id, family_id, token_hash, active_role, expires_at) "
        "VALUES (:u, :f, :h, 'client', now() + interval '1 day')",
        u=uid,
        f=str(uuid.uuid4()),
        h="a" * 64,
    )
    data = {"user_id": uid, "requested_at": "2026-10-21T08:00:00Z"}
    run_handler("user.deletion_requested", data)
    run_handler("user.deletion_requested", data)  # takror — effekt bir marta
    row = db.all("SELECT phone, is_active, deactivated_reason FROM users WHERE id = :u", u=uid)[0]
    assert tuple(row) == (None, False, "deletion_requested")
    assert db.scalar("SELECT revoked_reason FROM refresh_tokens WHERE user_id = :u", u=uid) == "deactivated"
    events = db.all("SELECT payload FROM outbox_events WHERE event_type = 'user.deactivated'")
    assert [e[0]["data"] for e in events] == [{"user_id": uid, "reason": "deletion_requested"}]
    # Shu raqam endi bo'sh — yangi akkaunt yaratish mumkin
    create_user(db)
    run_handler("user.deletion_requested", {"user_id": str(uuid.uuid4()), "requested_at": "2026-10-21T08:00:00Z"})


def test_scheduler_cleanup(db) -> None:
    import asyncio

    from tests.conftest import reset_caches

    uid = create_user(db)
    db.scalar(
        "INSERT INTO otp_codes (id, phone, code_hash, expires_at, created_at) VALUES "
        "(:id, '+998901234567', :h, now(), now() - interval '2 days')",
        id=str(uuid.uuid4()),
        h="b" * 64,
    )
    db.scalar(
        "INSERT INTO refresh_tokens (user_id, family_id, token_hash, active_role, expires_at) "
        "VALUES (:u, :f, :h, 'client', now() - interval '1 minute')",
        u=uid,
        f=str(uuid.uuid4()),
        h="c" * 64,
    )
    import os

    os.environ["DATABASE_URL"] = db.url.replace("+asyncpg", "")
    reset_caches()
    try:
        from app.core.database import get_engine
        from app.workers.scheduler import cleanup

        async def run() -> dict[str, int]:
            try:
                return await cleanup()
            finally:
                await get_engine().dispose()

        assert asyncio.run(run()) == {"otp_codes": 1, "refresh_tokens": 1}
    finally:
        del os.environ["DATABASE_URL"]
        reset_caches()
