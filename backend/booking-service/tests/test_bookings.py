"""Bron sagasi va holat mashinasi — haqiqiy Postgres + Redis, upstream'lar — tests/fakes.py.
Docker talab qiladi: `pytest -m integration`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token, sample_data

from tests.fakes import CLINIC, DOCTOR, HOME_SERVICE, SERVICE, FakeDeps

pytestmark = pytest.mark.integration


@pytest.fixture
def fakes(api: TestClient, run_handler) -> FakeDeps:
    deps: FakeDeps = run_handler.deps
    api.app.state.deps = deps
    return deps


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def events(db, event_type: str) -> list[dict[str, Any]]:
    rows = db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
    for (payload,) in rows:
        assert_event_valid(payload)
    return [r[0]["data"] for r in rows]


class Client:
    def __init__(self, api: TestClient, fakes: FakeDeps, user_keys) -> None:
        self.id = uuid.uuid4()
        self.api, self.fakes = api, fakes
        self.h = bearer(mint_user_token(user_keys, user_id=self.id))
        self.patient = fakes.users.add_patient(self.id)

    def book(self, slot: uuid.UUID, *, service: uuid.UUID = SERVICE, key: str | None = None, **extra: Any):
        body = {
            "patient_id": str(self.patient),
            "doctor_id": str(DOCTOR),
            "slot_id": str(slot),
            "service_ids": [str(service)],
            **extra,
        }
        headers = {**self.h, **({"Idempotency-Key": key} if key else {})}
        return self.api.post("/v1/bookings", json=body, headers=headers)


@pytest.fixture
def client(api: TestClient, fakes: FakeDeps, user_keys) -> Client:
    return Client(api, fakes, user_keys)


def later(hours: float = 48) -> datetime:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(microsecond=0)


def test_prepaid_booking_is_pending_with_snapshot(client: Client, fakes: FakeDeps, db) -> None:
    slot = fakes.schedule.add(later(), adjust_percent=-20)
    r = client.book(slot, comment="Iltimos, vaqtida")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "pending_payment" and b["payment_mode"] == "prepaid"
    assert b["original_tiyin"] == 12_000_000 and b["total_tiyin"] == b["prepay_tiyin"] == 12_000_000
    assert b["price_adjust_percent"] == -20 and b["number"].startswith("MB-")
    assert b["patient"]["full_name"] == "Ali Valiyev" and b["items"][0]["price_tiyin"] == 12_000_000
    assert fakes.schedule.slots[slot]["status"] == "held"
    assert fakes.schedule.slots[slot]["hold_ref"] == b["id"]
    created = events(db, "booking.created")
    assert len(created) == 1 and created[0]["status"] == "pending_payment"
    assert (
        created[0]["money"]["platform_fee_tiyin"]
        + created[0]["money"]["provider_fee_tiyin"]
        + created[0]["money"]["doctor_payout_tiyin"]
        == 12_000_000
    )
    assert events(db, "booking.confirmed") == []


def test_free_booking_confirmed_immediately(client: Client, fakes: FakeDeps, db) -> None:
    slot = fakes.schedule.add(later())
    b = client.book(slot, promo_code="free").json()
    assert b["status"] == "confirmed" and b["payment_mode"] == "at_clinic" and b["total_tiyin"] == 0
    assert fakes.schedule.slots[slot]["status"] == "booked"
    assert len(events(db, "booking.created")) == len(events(db, "booking.confirmed")) == 1


def test_idempotency_replay_mismatch_and_parallel_safety(client: Client, fakes: FakeDeps, db) -> None:
    slot = fakes.schedule.add(later())
    first = client.book(slot, key="k-1")
    assert first.status_code == 201
    again = client.book(slot, key="k-1")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    other = client.book(fakes.schedule.add(later(50)), key="k-1")
    assert other.status_code == 422 and other.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert db.scalar("SELECT count(*) FROM bookings") == 1
    # Saga yiqilsa kalit bo'shatiladi — qayta urinish mumkin
    fakes.pricing.commit_conflict = True
    bad = client.book(fakes.schedule.add(later(52)), key="k-2")
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "PROMO_INVALID"
    assert db.scalar("SELECT count(*) FROM idempotency_keys WHERE key = 'k-2'") == 0


def test_slot_taken_returns_alternatives_and_compensates(client: Client, fakes: FakeDeps, user_keys, api) -> None:
    slot = fakes.schedule.add(later())
    alt = fakes.schedule.add(later(49))
    assert client.book(slot).status_code == 201
    rival = Client(api, fakes, user_keys)
    r = rival.book(slot)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SLOT_TAKEN"
    assert r.json()["detail"]["alternatives"][0]["slot_id"] == str(alt)
    # pricing commit yiqilsa — hold qaytariladi (kompensatsiya)
    fakes.pricing.commit_conflict = True
    third = fakes.schedule.add(later(60))
    assert rival.book(third).status_code == 400
    assert fakes.schedule.slots[third]["status"] == "free"
    assert ("release", third) in fakes.schedule.calls


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("lead_time", "juda yaqin"),
        ("wrong_place", "joyi"),
        ("too_long", "sig'maydi"),
        ("foreign_patient", "patient_id"),
        ("not_bookable", "DOCTOR_NOT_BOOKABLE"),
        ("bad_promo", "PROMO_INVALID"),
        ("child", "bolalarni"),
        ("unknown_slot", "slot_id"),
    ],
)
def test_validation(client: Client, fakes: FakeDeps, setup: str, expected: str) -> None:
    slot = fakes.schedule.add(later())
    extra: dict[str, Any] = {}
    if setup == "lead_time":
        slot = fakes.schedule.add(later(0.5))
    elif setup == "wrong_place":
        slot = fakes.schedule.add(later(), place="home")
    elif setup == "too_long":
        slot = fakes.schedule.add(later(), minutes=20)
    elif setup == "foreign_patient":
        client.patient = uuid.uuid4()
    elif setup == "not_bookable":
        fakes.catalog.doctor["bookable"] = False
    elif setup == "bad_promo":
        extra["promo_code"] = "BAD"
    elif setup == "child":
        fakes.catalog.services[SERVICE]["patient_rules"]["accepts_children"] = False
        client.patient = fakes.users.add_patient(client.id, birth="2020-01-01")
    elif setup == "unknown_slot":
        slot = uuid.uuid4()
    r = client.book(slot, **extra)
    assert r.status_code in (400, 404), r.text
    assert expected in r.text
    assert all(s["status"] == "free" for s in fakes.schedule.slots.values())


def test_home_visit_radius_buffer_and_address_snapshot(client: Client, fakes: FakeDeps) -> None:
    slot = fakes.schedule.add(later(), place="home", minutes=60)
    no_address = client.book(slot, service=HOME_SERVICE)
    assert no_address.status_code == 400 and "manzil" in no_address.text
    far = fakes.users.add_address(client.id, lat="40.0", lng="71.0")
    assert "bormaydi" in client.book(slot, service=HOME_SERVICE, address_id=str(far)).text
    near = fakes.users.add_address(client.id)
    r = client.book(slot, service=HOME_SERVICE, address_id=str(near))
    assert r.status_code == 201, r.text
    assert r.json()["address"]["apartment"] == "12" and r.json()["payment_mode"] == "prepaid"
    assert 15 <= fakes.schedule.slots[slot]["travel_buffer"] <= 20  # ~1.4 km → 10 + 3*km


def test_pending_limit_and_patient_overlap(client: Client, fakes: FakeDeps) -> None:
    starts = [later(48 + i) for i in range(4)]
    for start in starts[:3]:
        client.patient = client.fakes.users.add_patient(client.id)
        assert client.book(fakes.schedule.add(start)).status_code == 201
    r = client.book(fakes.schedule.add(starts[3]))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TOO_MANY_PENDING"


def test_patient_cannot_double_book(client: Client, fakes: FakeDeps) -> None:
    start = later()
    assert client.book(fakes.schedule.add(start), promo_code="FREE").status_code == 201
    r = client.book(fakes.schedule.add(start + timedelta(minutes=10)), promo_code="FREE")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PATIENT_BUSY"


def test_cancellation_policy(client: Client, fakes: FakeDeps, db, run_handler) -> None:
    # 1) to'lanmagan — qaytarish 0
    b1 = client.book(fakes.schedule.add(later())).json()
    preview = client.api.get(f"/v1/bookings/{b1['id']}/cancellation-preview", headers=client.h).json()
    assert preview == {"allowed": True, "refund_tiyin": 0, "penalty_tiyin": 0, "reason_code": "unpaid"}
    r = client.api.post(f"/v1/bookings/{b1['id']}/cancel", json={"reason": "reja o'zgardi"}, headers=client.h)
    assert r.json()["status"] == "cancelled"
    # takror — o'zgarishsiz
    assert client.api.post(f"/v1/bookings/{b1['id']}/cancel", json={}, headers=client.h).status_code == 200
    assert len(events(db, "booking.cancelled")) == 1

    # 2) to'langan, 10 soat qolgan — 50%
    client.patient = fakes.users.add_patient(client.id)
    b2 = client.book(fakes.schedule.add(later(10))).json()
    run_handler(
        "payment.captured",
        sample_data("payment.captured", booking_id=b2["id"], client_id=str(client.id), amount_tiyin=b2["prepay_tiyin"]),
    )
    assert db.scalar("SELECT status FROM bookings WHERE id = :i", i=b2["id"]) == "confirmed"
    preview = client.api.get(f"/v1/bookings/{b2['id']}/cancellation-preview", headers=client.h).json()
    assert preview["reason_code"] == "partial_refund" and preview["refund_tiyin"] == b2["prepay_tiyin"] // 2
    client.api.post(f"/v1/bookings/{b2['id']}/cancel", json={}, headers=client.h)
    cancelled = events(db, "booking.cancelled")[-1]
    assert cancelled["refund_tiyin"] + cancelled["penalty_tiyin"] == b2["prepay_tiyin"]
    assert cancelled["previous_status"] == "confirmed" and cancelled["cancelled_by"] == "client"


def test_payment_confirm_and_confirmation_failed(client: Client, fakes: FakeDeps, db, run_handler) -> None:
    slot = fakes.schedule.add(later())
    b = client.book(slot).json()
    payment = sample_data(
        "payment.captured", booking_id=b["id"], client_id=str(client.id), amount_tiyin=b["prepay_tiyin"]
    )
    run_handler("payment.captured", payment)
    run_handler("payment.captured", payment)  # takror — ikkinchi confirmed yo'q
    assert fakes.schedule.slots[slot]["status"] == "booked"
    assert len(events(db, "booking.confirmed")) == 1
    assert db.scalar("SELECT paid_tiyin FROM bookings WHERE id = :i", i=b["id"]) == b["prepay_tiyin"]

    # Hold yo'qolgan (muddat o'tdi va slot boshqaga ketdi) — pul yechilgan, bron yo'q
    client.patient = fakes.users.add_patient(client.id)
    slot2 = fakes.schedule.add(later(60))
    b2 = client.book(slot2).json()
    fakes.schedule.slots[slot2].update(hold_ref="someone-else")
    run_handler(
        "payment.captured",
        sample_data("payment.captured", booking_id=b2["id"], client_id=str(client.id), amount_tiyin=b2["prepay_tiyin"]),
    )
    failed = events(db, "booking.confirmation_failed")
    assert failed[-1]["reason"] == "slot_unavailable" and failed[-1]["amount_tiyin"] == b2["prepay_tiyin"]
    assert db.scalar("SELECT status FROM bookings WHERE id = :i", i=b2["id"]) == "expired"

    # Kechikkan to'lov (bron allaqachon bekor) — ham confirmation_failed
    client.patient = fakes.users.add_patient(client.id)
    b3 = client.book(fakes.schedule.add(later(70))).json()
    client.api.post(f"/v1/bookings/{b3['id']}/cancel", json={}, headers=client.h)
    run_handler("payment.captured", sample_data("payment.captured", booking_id=b3["id"], amount_tiyin=1))
    assert events(db, "booking.confirmation_failed")[-1]["reason"] == "booking_cancelled"


def test_expiry_job_and_slot_released(client: Client, fakes: FakeDeps, db, run_handler) -> None:
    import asyncio

    from tests.conftest import reset_caches

    slot = fakes.schedule.add(later())
    b = client.book(slot).json()
    db.scalar("UPDATE bookings SET payment_due_at = now() - interval '1 minute' WHERE id = :i", i=b["id"])

    # `api` fixture DATABASE_URL ni o'rnatgan; engine keshi boshqa event loop'ga bog'langan — yangilaymiz
    reset_caches()
    try:
        from app.core.database import get_engine
        from app.workers.scheduler import expire_unpaid

        async def run() -> int:
            try:
                return await expire_unpaid()
            finally:
                await get_engine().dispose()

        assert asyncio.run(run()) == 1
    finally:
        reset_caches()
    assert db.scalar("SELECT status FROM bookings WHERE id = :i", i=b["id"]) == "expired"
    assert events(db, "booking.expired")[0]["booking_id"] == b["id"]

    # schedule hold muddati o'tganini aytdi (slot.released) — mos pending bron yopiladi
    client.patient = fakes.users.add_patient(client.id)
    slot2 = fakes.schedule.add(later(30))
    b2 = client.book(slot2).json()
    run_handler(
        "slot.released",
        sample_data(
            "slot.released",
            slot_id=str(slot2),
            doctor_id=str(DOCTOR),
            hold_ref=b2["id"],
            reason="hold_expired",
            place="clinic",
            start_at=later(30).isoformat(),
        ),
    )
    assert db.scalar("SELECT status FROM bookings WHERE id = :i", i=b2["id"]) == "expired"


def test_stale_pending_on_reused_slot_is_expired(client: Client, fakes: FakeDeps, db, user_keys, api) -> None:
    slot = fakes.schedule.add(later())
    old = client.book(slot).json()
    # Hold muddati o'tdi (schedule lazy expiry), lekin job hali bronni yopmagan
    fakes.schedule.slots[slot]["hold_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    db.scalar("UPDATE bookings SET payment_due_at = now() - interval '1 second' WHERE id = :i", i=old["id"])
    rival = Client(api, fakes, user_keys)
    r = rival.book(slot)
    assert r.status_code == 201, r.text
    assert db.scalar("SELECT status FROM bookings WHERE id = :i", i=old["id"]) == "expired"


def test_reschedule_rules(client: Client, fakes: FakeDeps, db) -> None:
    old = fakes.schedule.add(later(72))
    b = client.book(old, promo_code="FREE").json()
    new = fakes.schedule.add(later(96))
    r = client.api.post(f"/v1/bookings/{b['id']}/reschedule", json={"new_slot_id": str(new)}, headers=client.h)
    assert r.status_code == 200, r.text
    assert r.json()["rescheduled_count"] == 1 and r.json()["start_at"].startswith(later(96).date().isoformat())
    assert fakes.schedule.slots[new]["status"] == "booked"
    ev = events(db, "booking.rescheduled")[0]
    assert ev["old_slot_id"] == str(old) and ev["new_slot_id"] == str(new)
    taken = fakes.schedule.add(later(100))
    fakes.schedule.slots[taken].update(status="booked", hold_ref="x")
    r = client.api.post(f"/v1/bookings/{b['id']}/reschedule", json={"new_slot_id": str(taken)}, headers=client.h)
    assert r.status_code == 409
    r = client.api.post(
        f"/v1/bookings/{b['id']}/reschedule",
        json={"new_slot_id": str(fakes.schedule.add(later(120)))},
        headers=client.h,
    )
    assert r.status_code == 200
    r = client.api.post(
        f"/v1/bookings/{b['id']}/reschedule",
        json={"new_slot_id": str(fakes.schedule.add(later(130)))},
        headers=client.h,
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "RESCHEDULE_LIMIT"
    # Qabulga < 24 soat
    client.patient = fakes.users.add_patient(client.id)
    soon = client.book(fakes.schedule.add(later(5)), promo_code="FREE").json()
    r = client.api.post(
        f"/v1/bookings/{soon['id']}/reschedule",
        json={"new_slot_id": str(fakes.schedule.add(later(140)))},
        headers=client.h,
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TOO_LATE"


def test_doctor_flow_start_complete_no_show_cancel(client: Client, fakes: FakeDeps, db, user_keys) -> None:
    patient_only = bearer(mint_user_token(user_keys))
    r = client.api.get("/v1/doctor/appointments/today", headers=patient_only)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "NOT_A_DOCTOR"
    doctor = bearer(mint_user_token(user_keys, roles=["client", "doctor"], doctor_id=DOCTOR))
    other_doctor = bearer(mint_user_token(user_keys, roles=["client", "doctor"], doctor_id=uuid.uuid4()))
    b = client.book(fakes.schedule.add(later(48)), promo_code="FREE").json()
    db.scalar(
        "UPDATE bookings SET start_at = now() - interval '5 minutes', end_at = now() + interval '25 minutes' "
        "WHERE id = :i",
        i=b["id"],
    )
    assert client.api.get(f"/v1/doctor/appointments/{b['id']}", headers=other_doctor).status_code == 404
    assert client.api.get("/v1/doctor/appointments/today", headers=doctor).json()[0]["id"] == b["id"]
    detail = client.api.get(f"/v1/doctor/appointments/{b['id']}", headers=doctor).json()
    assert detail["patient"]["full_name"] == "Ali Valiyev" and detail["to_collect_tiyin"] == 0
    assert client.api.post(f"/v1/doctor/appointments/{b['id']}/start", headers=doctor).json()["status"] == "in_progress"
    done = client.api.post(f"/v1/doctor/appointments/{b['id']}/complete", json={"note": "Sog'lom"}, headers=doctor)
    assert done.json()["status"] == "completed" and done.json()["note"] == "Sog'lom"
    assert events(db, "booking.started")[0]["booking_id"] == b["id"]
    assert events(db, "booking.completed")[0]["completed_by"] == "doctor"

    client.patient = fakes.users.add_patient(client.id)
    b2 = client.book(fakes.schedule.add(later(49)), promo_code="FREE").json()
    early = client.api.post(f"/v1/doctor/appointments/{b2['id']}/no-show", json={}, headers=doctor)
    assert early.status_code == 409 and early.json()["detail"]["code"] == "NOT_STARTED"
    db.scalar("UPDATE bookings SET start_at = now() - interval '40 minutes' WHERE id = :i", i=b2["id"])
    r = client.api.post(f"/v1/doctor/appointments/{b2['id']}/no-show", json={"note": "kelmadi"}, headers=doctor)
    assert r.json()["status"] == "no_show"
    assert events(db, "booking.no_show")[0]["no_show_by"] == "client"

    client.patient = fakes.users.add_patient(client.id)
    b3 = client.book(fakes.schedule.add(later(50)), promo_code="FREE").json()
    assert (
        client.api.post(f"/v1/doctor/appointments/{b3['id']}/cancel", json={"reason": ""}, headers=doctor).status_code
        == 422
    )
    r = client.api.post(f"/v1/doctor/appointments/{b3['id']}/cancel", json={"reason": "kasal"}, headers=doctor)
    assert r.json()["status"] == "cancelled"
    assert events(db, "booking.cancelled")[-1]["cancelled_by"] == "doctor"
    audit = [e["action"] for e in events(db, "audit.recorded")]
    assert {"booking.complete", "booking.no_show", "booking.cancel"} <= set(audit)


def test_system_cancellations_from_catalog_events(client: Client, fakes: FakeDeps, db, run_handler) -> None:
    ids = []
    for hours in (48, 60):
        client.patient = fakes.users.add_patient(client.id)
        ids.append(client.book(fakes.schedule.add(later(hours)), promo_code="FREE").json()["id"])
    run_handler("affiliation.ended", sample_data("affiliation.ended", doctor_id=str(DOCTOR), clinic_id=str(CLINIC)))
    statuses = {r[0] for r in db.all("SELECT status FROM bookings")}
    assert statuses == {"cancelled"}
    reasons = {e["reason_code"] for e in events(db, "booking.cancelled")}
    assert reasons == {"affiliation_ended"}
    for event_type, payload in (
        ("doctor.suspended", {"doctor_id": str(DOCTOR)}),
        ("clinic.suspended", {"clinic_id": str(CLINIC)}),
        ("user.deletion_requested", {"user_id": str(client.id)}),
    ):
        client.patient = fakes.users.add_patient(client.id)
        bid = client.book(fakes.schedule.add(later(80)), promo_code="FREE").json()["id"]
        run_handler(event_type, sample_data(event_type, **payload))
        assert db.scalar("SELECT status FROM bookings WHERE id = :i", i=bid) == "cancelled"
    run_handler("admin.settings_changed", sample_data("admin.settings_changed"))
    assert fakes.settings.invalidated == 1


def test_waitlist_join_offer_and_convert(client: Client, fakes: FakeDeps, db, run_handler) -> None:
    start = later(72)
    body = {"doctor_id": str(DOCTOR), "date_from": start.date().isoformat(), "date_to": start.date().isoformat()}
    entry = client.api.post("/v1/waitlist", json=body, headers=client.h).json()
    assert client.api.post("/v1/waitlist", json=body, headers=client.h).json()["id"] == entry["id"]
    freed = sample_data(
        "slot.released", doctor_id=str(DOCTOR), place="clinic", start_at=start.isoformat(), reason="booking_cancelled"
    )
    run_handler("slot.released", freed)
    offered = events(db, "waitlist.slot_offered")
    assert offered[0]["client_id"] == str(client.id)
    run_handler("slot.released", freed)  # 15 daq cooldown — takror taklif yo'q
    assert len(events(db, "waitlist.slot_offered")) == 1
    assert client.book(fakes.schedule.add(start), promo_code="FREE").status_code == 201
    assert client.api.get("/v1/waitlist", headers=client.h).json() == []  # converted
    second = client.api.post("/v1/waitlist", json=body, headers=client.h).json()
    assert client.api.delete(f"/v1/waitlist/{second['id']}", headers=client.h).status_code == 204
    past = {**body, "date_from": "2020-01-01"}
    assert client.api.post("/v1/waitlist", json=past, headers=client.h).status_code == 400


def test_mine_pagination_detail_rebook(client: Client, fakes: FakeDeps, user_keys, api) -> None:
    ids = []
    for hours in (48, 50, 52):
        client.patient = fakes.users.add_patient(client.id)
        ids.append(client.book(fakes.schedule.add(later(hours)), promo_code="FREE").json()["id"])
    page = api.get("/v1/bookings/mine", params={"limit": 2}, headers=client.h).json()
    assert [b["id"] for b in page["results"]] == ids[::-1][:2] and page["next_cursor"]
    rest = api.get("/v1/bookings/mine", params={"limit": 2, "cursor": page["next_cursor"]}, headers=client.h).json()
    assert [b["id"] for b in rest["results"]] == [ids[0]] and rest["next_cursor"] is None
    assert api.get("/v1/bookings/mine", params={"status": "bogus"}, headers=client.h).status_code == 400
    stranger = bearer(mint_user_token(user_keys))
    assert api.get(f"/v1/bookings/{ids[0]}", headers=stranger).status_code == 404
    fakes.schedule.add(later(100))
    rebook = api.get(f"/v1/bookings/{ids[0]}/rebook", headers=client.h).json()
    assert rebook["doctor_bookable"] and rebook["service_ids"] == [str(SERVICE)] and rebook["slots"]


def test_admin_and_clinic_views(client: Client, fakes: FakeDeps, db, admin_keys, user_keys, api) -> None:
    b = client.book(fakes.schedule.add(later()), promo_code="FREE").json()
    reader = bearer(mint_admin_token(admin_keys, permissions=["bookings.read"]))
    manager = bearer(mint_admin_token(admin_keys, permissions=["bookings.manage"]))
    assert (
        api.get("/v1/admin/bookings", params={"number": b["number"].lower()}, headers=reader).json()[0]["id"] == b["id"]
    )
    assert api.post(f"/v1/admin/bookings/{b['id']}/cancel", json={"reason": "x"}, headers=reader).status_code == 403
    r = api.post(f"/v1/admin/bookings/{b['id']}/cancel", json={"reason": "nizo"}, headers=manager)
    assert r.json()["status"] == "cancelled" and events(db, "booking.cancelled")[-1]["cancelled_by"] == "admin"

    outsider = bearer(mint_user_token(user_keys, roles=["client", "clinic_admin"]))
    day = later().date().isoformat()
    assert api.get(f"/v1/clinic/{CLINIC}/appointments", params={"date": day}, headers=outsider).status_code == 404
    clinic_admin = uuid.uuid4()
    fakes.catalog.clinic_admins.add((clinic_admin, CLINIC))
    h = bearer(mint_user_token(user_keys, user_id=clinic_admin, roles=["client", "clinic_admin"]))
    rows = api.get(f"/v1/clinic/{CLINIC}/appointments", params={"date": day}, headers=h).json()
    assert [r["id"] for r in rows] == [b["id"]]


def test_internal_endpoints(client: Client, fakes: FakeDeps, api) -> None:
    b = client.book(fakes.schedule.add(later())).json()
    payment = {"X-Caller": "payment-service", "X-Internal-Token": "test-token"}
    payable = api.get(f"/internal/v1/bookings/{b['id']}/payable", headers=payment).json()
    assert payable["payable"] is True and payable["prepay_tiyin"] == b["prepay_tiyin"]
    assert payable["items"][0]["mxik_code"] == "06101001001000000"  # bo'sh kod — admin standarti
    r = api.post(f"/internal/v1/bookings/{b['id']}/extend-payment", headers=payment)
    assert r.status_code == 200 and r.json()["payment_due_at"] >= b["payment_due_at"]
    review = {"X-Caller": "review-service", "X-Internal-Token": "test-token"}
    elig = api.get(
        f"/internal/v1/bookings/{b['id']}/review-eligibility", params={"client_id": str(client.id)}, headers=review
    ).json()
    assert elig == {"eligible": False, "reason": "not_completed", "doctor_id": str(DOCTOR), "completed_at": None}
    dispute = {"X-Caller": "dispute-service", "X-Internal-Token": "test-token"}
    assert (
        api.get(
            f"/internal/v1/bookings/{b['id']}/summary", params={"user_id": str(uuid.uuid4())}, headers=dispute
        ).status_code
        == 404
    )
    ws = {"X-Caller": "ws-gateway", "X-Internal-Token": "test-token"}
    assert api.get(f"/internal/v1/bookings/{b['id']}/authz", params={"user_id": str(client.id)}, headers=ws).json() == {
        "allowed": True
    }
    user = {"X-Caller": "user-service", "X-Internal-Token": "test-token"}
    assert api.get(f"/internal/v1/bookings/{b['id']}/payable", headers=user).status_code in (401, 403)


def test_every_catalog_consume_has_handler() -> None:
    from medbron_common.eventbus import EventConsumer
    from medbron_common.events import CATALOG

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="booking-service", amqp_url="amqp://x/", session_factory=None, processed_model=ProcessedEvent
    )
    handlers.register(consumer, FakeDeps())  # type: ignore[arg-type]
    assert consumer.routing_keys == sorted(e for e, s in CATALOG.items() if "booking-service" in s.consumers)


def test_admin_complete_no_show_and_auto_complete(client: Client, fakes: FakeDeps, db, admin_keys, api) -> None:
    import asyncio

    from tests.conftest import reset_caches

    manager = bearer(mint_admin_token(admin_keys, permissions=["bookings.manage", "bookings.read"]))
    ids = []
    for hours in (48, 50, 52):
        client.patient = fakes.users.add_patient(client.id)
        ids.append(client.book(fakes.schedule.add(later(hours)), promo_code="FREE").json()["id"])
    db.scalar("UPDATE bookings SET start_at = now() - interval '30 hours', end_at = now() - interval '29 hours'")
    assert api.get(f"/v1/admin/bookings/{ids[0]}", headers=manager).json()["status"] == "confirmed"
    assert api.get(f"/v1/admin/bookings/{uuid.uuid4()}", headers=manager).status_code == 404
    r = api.post(f"/v1/admin/bookings/{ids[0]}/no-show", json={"absent": "doctor", "note": "kelmadi"}, headers=manager)
    assert r.json()["status"] == "no_show"
    assert events(db, "booking.no_show")[0]["no_show_by"] == "doctor"
    r = api.post(f"/v1/admin/bookings/{ids[1]}/complete", json={"note": "ok"}, headers=manager)
    assert r.json()["status"] == "completed"

    reset_caches()
    try:
        from app.core.database import get_engine
        from app.workers.scheduler import auto_complete, housekeeping

        async def run() -> tuple[int, dict[str, int]]:
            try:
                return await auto_complete(), await housekeeping()
            finally:
                await get_engine().dispose()

        completed, house = asyncio.run(run())
    finally:
        reset_caches()
    assert completed == 1 and set(house) == {"waitlist_expired", "idempotency_keys"}
    assert db.scalar("SELECT completed_by FROM bookings WHERE id = :i", i=ids[2]) == "system"
