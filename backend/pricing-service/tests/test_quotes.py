"""Quote/commit, chegirmalar, band qilishlar hayot sikli, paketlar, promo admin — haqiqiy Postgres + Redis.
Docker talab qiladi: `pytest -m integration`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token, sample_data

pytestmark = pytest.mark.integration

INTERNAL = {"X-Caller": "booking-service", "X-Internal-Token": "test-token"}
SOM = 100
START = (datetime.now(UTC) + timedelta(days=2)).isoformat()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def quote(api: TestClient, client_id: Any, doctor_id: Any, service_ids: list[Any], **kw: Any) -> Any:
    body = {
        "client_id": str(client_id),
        "doctor_id": str(doctor_id),
        "service_ids": [str(s) for s in service_ids],
        "slot_start_at": START,
        "price_adjust_percent": kw.pop("pct", 0),
        "place": kw.pop("place", "clinic"),
        **kw,
    }
    return api.post("/internal/v1/quotes", json=body, headers=INTERNAL)


def commit(api: TestClient, quote_id: str, client_id: Any, booking_id: Any = None) -> Any:
    body = {"booking_id": str(booking_id or uuid.uuid4()), "client_id": str(client_id)}
    return api.post(f"/internal/v1/quotes/{quote_id}/commit", json=body, headers=INTERNAL)


def admin_promo(api: TestClient, admin_keys: Any, **kw: Any) -> dict[str, Any]:
    body = {"code": " bahor ", "kind": "percent", "value": 20, **kw}
    r = api.post(
        "/v1/admin/promo-codes", json=body, headers=bearer(mint_admin_token(admin_keys, permissions=["pricing.manage"]))
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def booking_event(
    run_handler: Any, event_type: str, booking_id: Any, client_id: Any, doctor_id: Any, **kw: Any
) -> None:
    at = kw.pop("_at", None)
    ids = {"booking_id": str(booking_id), "client_id": str(client_id), "doctor_id": str(doctor_id)}
    run_handler(event_type, sample_data(event_type, **ids, **kw), occurred_at=at)


def test_quote_shape_split_and_fiscal_defaults(api: TestClient, world, db) -> None:
    doctor = world.catalog.add_doctor()
    s1 = world.catalog.add_service(doctor, price_tiyin=100_000 * SOM)
    s2 = world.catalog.add_service(doctor, price_tiyin=50_000 * SOM, mxik="11111")
    r = quote(api, uuid.uuid4(), doctor, [s1, s2], pct=-20)
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["original_tiyin"] == 120_000 * SOM and q["price_adjust_percent"] == -20
    assert [i["price_tiyin"] for i in q["items"]] == [80_000 * SOM, 40_000 * SOM]
    assert [i["base_price_tiyin"] for i in q["items"]] == [100_000 * SOM, 50_000 * SOM]
    assert [i["mxik_code"] for i in q["items"]] == [world.settings["fiscal"]["default_mxik_code"], "11111"]
    assert q["discount_kind"] == "" and q["total_tiyin"] == 120_000 * SOM
    m = q["money"]
    assert m["platform_fee_tiyin"] + m["provider_fee_tiyin"] + m["doctor_payout_tiyin"] == q["total_tiyin"]
    assert (q["payment_mode"], q["prepay_tiyin"], q["commission_bp"], q["provider_fee_bp"]) == (
        "prepaid", 120_000 * SOM, 1500, 100)  # fmt: skip
    assert "_reserve" not in q
    # Xizmatlar proyeksiyaga yozildi — ikkinchi quote catalog'ni chaqirmaydi
    calls = world.catalog.service_calls
    assert quote(api, uuid.uuid4(), doctor, [s1]).status_code == 200
    assert world.catalog.service_calls == calls
    assert db.scalar("SELECT mxik_code FROM services_cache WHERE service_id = :s", s=str(s2)) == "11111"


def test_services_and_doctor_validation(api: TestClient, world) -> None:
    doctor, other = world.catalog.add_doctor(), world.catalog.add_doctor()
    mine = world.catalog.add_service(doctor)
    home = world.catalog.add_service(doctor, place="home")
    foreign = world.catalog.add_service(other)
    inactive = world.catalog.add_service(doctor, active=False)
    for ids in ([foreign], [mine, home], [inactive], [mine, mine], [uuid.uuid4()]):
        r = quote(api, uuid.uuid4(), doctor, ids)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "SERVICES_UNAVAILABLE", ids
    assert quote(api, uuid.uuid4(), doctor, [mine], place="home").status_code == 422
    assert quote(api, uuid.uuid4(), uuid.uuid4(), [mine]).json()["detail"]["code"] == "DOCTOR_NOT_BOOKABLE"
    off = world.catalog.add_doctor(bookable=False)
    assert (
        quote(api, uuid.uuid4(), off, [world.catalog.add_service(off)]).json()["detail"]["code"]
        == "DOCTOR_NOT_BOOKABLE"
    )
    # Uy chaqiruvi — shifokor rejimidan qat'i nazar oldindan to'lov
    clinic_pay = world.catalog.add_doctor(mode="at_clinic")
    r = quote(api, uuid.uuid4(), clinic_pay, [world.catalog.add_service(clinic_pay, place="home")], place="home")
    assert r.json()["payment_mode"] == "prepaid"


def test_promo_lifecycle_limits_and_release(api: TestClient, world, db, admin_keys, run_handler) -> None:
    doctor = world.catalog.add_doctor()
    service = world.catalog.add_service(doctor)
    promo = admin_promo(api, admin_keys, max_uses=1)
    assert promo["code"] == "BAHOR"
    alice, bob = uuid.uuid4(), uuid.uuid4()

    q = quote(api, alice, doctor, [service], promo_code=" Bahor ").json()
    assert (q["discount_kind"], q["discount_tiyin"], q["promo_code"]) == ("promo", 30_000 * SOM, "BAHOR")
    booking = uuid.uuid4()
    assert commit(api, q["quote_id"], alice, booking).status_code == 200
    assert commit(api, q["quote_id"], alice, booking).status_code == 200  # idempotent
    assert db.scalar("SELECT count(*) FROM promo_redemptions") == 1
    assert db.scalar("SELECT state FROM promo_redemptions") == "reserved"

    # Band qilingan (reserved) ham limitga kiradi — boshqa mijoz ololmaydi
    r = quote(api, bob, doctor, [service], promo_code="BAHOR")
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "limit_reached"

    booking_event(run_handler, "booking.created", booking, alice, doctor, status="pending_payment")
    assert db.scalar("SELECT state FROM promo_redemptions") == "active"
    booking_event(run_handler, "booking.expired", booking, alice, doctor)
    assert db.scalar("SELECT state FROM promo_redemptions") == "released"
    q2 = quote(api, bob, doctor, [service], promo_code="BAHOR").json()
    assert q2["discount_kind"] == "promo"

    # Quote olingan, lekin oraliqda limit tugadi → commit 409
    q3 = quote(api, alice, doctor, [service], promo_code="BAHOR").json()
    assert commit(api, q2["quote_id"], bob).status_code == 200
    r = commit(api, q3["quote_id"], alice)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PROMO_LIMIT_REACHED"

    assert commit(api, str(uuid.uuid4()), alice).json()["detail"]["code"] == "QUOTE_EXPIRED"
    assert commit(api, q3["quote_id"], bob).status_code == 404  # begona quote


def test_invalid_promo_blocks_even_if_other_discount_wins(api: TestClient, world, admin_keys, run_handler) -> None:
    doctor = world.catalog.add_doctor(follow_up=50)
    service = world.catalog.add_service(doctor)
    client = uuid.uuid4()
    booking_event(run_handler, "booking.completed", uuid.uuid4(), client, doctor,
                  completed_at=datetime.now(UTC).isoformat())  # fmt: skip
    r = quote(api, client, doctor, [service], promo_code="NOPE")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "PROMO_INVALID"
    assert r.json()["detail"]["reason"] == "not_found"
    q = quote(api, client, doctor, [service]).json()
    assert (q["discount_kind"], q["discount_tiyin"]) == ("follow_up", 75_000 * SOM)
    assert q["money"]["discount_borne_by"] == "doctor"


def test_first_booking_and_no_shows(api: TestClient, world, run_handler) -> None:
    world.settings["pricing"]["first_booking_discount_percent"] = 10
    doctor = world.catalog.add_doctor(mode="at_clinic")
    service = world.catalog.add_service(doctor)
    client = uuid.uuid4()
    q = quote(api, client, doctor, [service]).json()
    assert (q["discount_kind"], q["discount_tiyin"], q["payment_mode"]) == ("first_booking", 15_000 * SOM, "at_clinic")
    assert q["money"]["discount_borne_by"] == "platform"

    t0 = datetime.now(UTC)
    for i in range(2):
        b = uuid.uuid4()
        booking_event(run_handler, "booking.created", b, client, doctor, status="confirmed", _at=t0)
        booking_event(run_handler, "booking.no_show", b, client, doctor, no_show_by="client",
                      _at=t0 + timedelta(minutes=1 + i))  # fmt: skip
    q = quote(api, client, doctor, [service]).json()
    assert q["discount_kind"] == "" and (q["payment_mode"], q["prepay_tiyin"]) == ("prepaid", 150_000 * SOM)


def test_projection_ignores_stale_events(api: TestClient, world, db, run_handler) -> None:
    doctor, client, booking = world.catalog.add_doctor(), uuid.uuid4(), uuid.uuid4()
    t0 = datetime.now(UTC)
    booking_event(run_handler, "booking.cancelled", booking, client, doctor, _at=t0 + timedelta(minutes=5))
    booking_event(run_handler, "booking.confirmed", booking, client, doctor, _at=t0)  # kechikkan eski event
    assert db.scalar("SELECT status FROM client_bookings WHERE booking_id = :b", b=str(booking)) == "cancelled"


def test_packages_enroll_use_exhaust_and_reactivate(api: TestClient, world, db, user_keys, run_handler) -> None:
    doctor = world.catalog.add_doctor()
    service = world.catalog.add_service(doctor)
    doctor_token = bearer(mint_user_token(user_keys, roles=["client", "doctor"], doctor_id=doctor))
    r = api.post("/v1/doctor/packages", json={"service_id": str(service), "name": "Kurs", "sessions": 2,
                                              "discount_percent": 10}, headers=doctor_token)  # fmt: skip
    assert r.status_code == 201, r.text
    package = r.json()
    assert package["total_saving_tiyin"] == 30_000 * SOM and package["unit_price_tiyin"] == 150_000 * SOM
    foreign = world.catalog.add_service(world.catalog.add_doctor())
    r = api.post("/v1/doctor/packages", json={"service_id": str(foreign), "name": "X", "sessions": 2,
                                              "discount_percent": 10}, headers=doctor_token)  # fmt: skip
    assert r.status_code == 422
    assert api.get("/v1/doctor/packages", headers=bearer(mint_user_token(user_keys))).status_code == 403
    assert [p["id"] for p in api.get("/v1/pricing/packages", params={"doctor_id": str(doctor)}).json()] == [
        package["id"]
    ]

    client = uuid.uuid4()
    ch = bearer(mint_user_token(user_keys, user_id=client))
    r = api.post(f"/v1/pricing/packages/{package['id']}/enroll", headers=ch)
    assert r.status_code == 201 and r.json()["sessions_left"] == 2
    assert api.post(f"/v1/pricing/packages/{package['id']}/enroll", headers=ch).json()["detail"]["code"] == (
        "PACKAGE_ALREADY_ACTIVE")  # fmt: skip

    bookings = []
    for _ in range(2):
        q = quote(api, client, doctor, [service]).json()
        assert (q["discount_kind"], q["discount_tiyin"]) == ("package", 15_000 * SOM)
        booking = uuid.uuid4()
        assert commit(api, q["quote_id"], client, booking).status_code == 200
        booking_event(run_handler, "booking.created", booking, client, doctor, status="pending_payment")
        bookings.append(booking)
    assert db.scalar("SELECT is_active FROM package_enrollments") is False  # seanslar tugadi
    assert quote(api, client, doctor, [service]).json()["discount_kind"] == ""
    assert api.get("/v1/pricing/packages/mine", headers=ch).json() == []

    # Bron bekor — seans qaytadi va yopilgan yozilish qayta faollashadi (ADR-0011 #5)
    booking_event(run_handler, "booking.cancelled", bookings[0], client, doctor)
    mine = api.get("/v1/pricing/packages/mine", headers=ch).json()
    assert len(mine) == 1 and mine[0]["sessions_left"] == 1
    assert quote(api, client, doctor, [service]).json()["discount_kind"] == "package"

    r = api.patch(f"/v1/doctor/packages/{package['id']}", json={"is_active": False}, headers=doctor_token)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (
        api.post(f"/v1/pricing/packages/{package['id']}/enroll", headers=bearer(mint_user_token(user_keys))).status_code
        == 404
    )


def test_sweeper_releases_orphan_reservations(api: TestClient, world, db, admin_keys) -> None:
    import asyncio

    from tests.conftest import reset_caches

    doctor = world.catalog.add_doctor()
    service = world.catalog.add_service(doctor)
    admin_promo(api, admin_keys, max_uses=1)
    client = uuid.uuid4()
    q = quote(api, client, doctor, [service], promo_code="BAHOR").json()
    commit(api, q["quote_id"], client)
    db.scalar("UPDATE promo_redemptions SET reserved_at = now() - interval '16 minutes'")

    import os

    os.environ["DATABASE_URL"] = db.url.replace("+asyncpg", "")
    reset_caches()
    try:
        from app.core.database import get_engine
        from app.workers.scheduler import sweep

        async def run() -> int:
            try:
                return await sweep()
            finally:
                await get_engine().dispose()

        assert asyncio.run(run()) == 1
    finally:
        del os.environ["DATABASE_URL"]
        reset_caches()
    assert db.scalar("SELECT state FROM promo_redemptions") == "released"


def test_public_preview_and_promo_validate(api: TestClient, world, admin_keys, user_keys) -> None:
    doctor = world.catalog.add_doctor()
    service = world.catalog.add_service(doctor)
    admin_promo(api, admin_keys, value=10)
    h = bearer(mint_user_token(user_keys))
    body = {"doctor_id": str(doctor), "service_ids": [str(service)], "promo_code": "bahor"}
    r = api.post("/v1/pricing/quote", json=body, headers=h)
    assert r.status_code == 200 and r.json()["discount_tiyin"] == 15_000 * SOM and "money" not in r.json()
    ok = api.post("/v1/pricing/promo/validate", json={"code": "BAHOR", "doctor_id": str(doctor),
                                                      "service_ids": [str(service)]}, headers=h).json()  # fmt: skip
    assert ok == {"valid": True, "reason": "", "message": "", "discount_tiyin": 15_000 * SOM}
    bad = api.post("/v1/pricing/promo/validate", json={"code": "XYZ", "doctor_id": str(doctor),
                                                       "service_ids": [str(service)]}, headers=h).json()  # fmt: skip
    assert bad["valid"] is False and bad["reason"] == "not_found"
    assert api.post("/v1/pricing/quote", json=body).status_code == 401


def test_admin_promo_crud_and_audit(api: TestClient, world, db, admin_keys) -> None:
    h = bearer(mint_admin_token(admin_keys, permissions=["pricing.manage"]))
    promo = admin_promo(api, admin_keys, kind="fixed", value=5_000 * SOM)
    assert (
        api.post("/v1/admin/promo-codes", json={"code": "BAHOR", "kind": "percent", "value": 10}, headers=h).status_code
        == 409
    )
    assert (
        api.post("/v1/admin/promo-codes", json={"code": "PCT", "kind": "percent", "value": 150}, headers=h).status_code
        == 422
    )
    r = api.patch(f"/v1/admin/promo-codes/{promo['id']}", json={"is_active": False}, headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert api.get(f"/v1/admin/promo-codes/{promo['id']}", headers=h).json()["uses"] == 0
    assert len(api.get("/v1/admin/promo-codes", headers=h).json()) == 1
    assert api.get(f"/v1/admin/promo-codes/{uuid.uuid4()}", headers=h).status_code == 404
    no = bearer(mint_admin_token(admin_keys, permissions=["users.manage"]))
    assert api.get("/v1/admin/promo-codes", headers=no).status_code == 403
    audits = [
        row[0] for row in db.all("SELECT payload FROM outbox_events WHERE event_type = 'audit.recorded' ORDER BY id")
    ]
    assert [a["data"]["action"] for a in audits] == ["promo.create", "promo.update"]
    assert audits[1]["data"]["after"] == {"is_active": False}
    for a in audits:
        assert_event_valid(a)


def test_service_events_and_user_deletion(api: TestClient, world, db, run_handler) -> None:
    doctor = world.catalog.add_doctor()
    sid = str(uuid.uuid4())
    base = sample_data(
        "service.created", service_id=sid, doctor_id=str(doctor), clinic_id=None, price_tiyin=40_000 * SOM,
        is_active=True, duration_minutes=30, name="Ko'rik",
    )  # fmt: skip
    t0 = datetime.now(UTC)
    run_handler("service.created", base, occurred_at=t0)
    run_handler("service.updated", sample_data("service.updated", **{**base, "price_tiyin": 50_000 * SOM}),
                occurred_at=t0 + timedelta(minutes=1))  # fmt: skip
    run_handler("service.created", base, occurred_at=t0)  # eski — e'tiborsiz
    assert db.scalar("SELECT price_tiyin FROM services_cache WHERE service_id = :s", s=sid) == 50_000 * SOM
    assert quote(api, uuid.uuid4(), doctor, [sid]).json()["original_tiyin"] == 50_000 * SOM
    run_handler("service.deactivated", sample_data("service.deactivated", service_id=sid, doctor_id=str(doctor)),
                occurred_at=t0 + timedelta(minutes=2))  # fmt: skip
    assert quote(api, uuid.uuid4(), doctor, [sid]).status_code == 422

    client = uuid.uuid4()
    booking_event(run_handler, "booking.confirmed", uuid.uuid4(), client, doctor)
    run_handler("user.deletion_requested", sample_data("user.deletion_requested", user_id=str(client)))
    assert db.scalar("SELECT count(*) FROM client_bookings WHERE client_id = :c", c=str(client)) == 0


def test_settings_changed_invalidates_cache(api: TestClient, world, run_handler) -> None:
    doctor = world.catalog.add_doctor()
    service = world.catalog.add_service(doctor)
    assert quote(api, uuid.uuid4(), doctor, [service]).json()["commission_bp"] == 1500
    world.settings["commission"]["platform_bp"] = 2000
    assert quote(api, uuid.uuid4(), doctor, [service]).json()["commission_bp"] == 1500  # kesh (60 s)
    from app.core.redis import get_redis

    get_redis.cache_clear()  # handler o'z event loop'ida yangi Redis client oladi (ilovaniki boshqa loop'da)
    try:
        run_handler("admin.settings_changed", sample_data("admin.settings_changed", keys=["commission"]))
    finally:
        get_redis.cache_clear()
    assert quote(api, uuid.uuid4(), doctor, [service]).json()["commission_bp"] == 2000


def test_every_catalog_consume_has_handler() -> None:
    from medbron_common.eventbus import EventConsumer
    from medbron_common.events import CATALOG

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="pricing-service", amqp_url="amqp://x/", session_factory=None, processed_model=ProcessedEvent
    )
    handlers.register(consumer)
    assert consumer.routing_keys == sorted(e for e, s in CATALOG.items() if "pricing-service" in s.consumers)
