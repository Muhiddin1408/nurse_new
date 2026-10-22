"""Ommaviy katalog, internal API, reyting eventi va bazasiz auth testlari."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token, sample_data

from tests.helpers import INTERNAL, admin_h, approved_doctor, bearer, make_clinic, make_spec, user_h


def test_health_and_auth_chain(client: TestClient, user_keys, admin_keys) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert "http_requests_total" in client.get("/metrics").text
    assert client.get("/v1/doctor/profile").status_code == 401
    r = client.get("/v1/doctor/profile", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/admin/clinics", headers=bearer(mint_user_token(user_keys)))
    assert r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/admin/specializations", headers=bearer(mint_admin_token(admin_keys, permissions=["x"])))
    assert r.status_code == 403
    path = f"/internal/v1/doctors/{uuid.uuid4()}/bookability"
    assert client.get(path).status_code == 401
    r = client.get(path, headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"})
    assert r.json()["detail"]["code"] == "CALLER_NOT_ALLOWED"


def test_working_hours_validation() -> None:
    from app.schemas.catalog import validate_working_hours

    assert validate_working_hours({"0": ["08:00", "18:00"], "6": None})
    for bad in ({"7": None}, {"0": ["08:00"]}, {"0": ["18:00", "08:00"]}):
        with pytest.raises(ValueError):
            validate_working_hours(bad)


@pytest.mark.integration
def test_public_listing_filters_and_rating(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin)
    other_spec = make_spec(api, admin, name="Pediatr", is_pediatric=True)
    near = make_clinic(api, admin, lat=41.311, lng=69.240)
    far = make_clinic(api, admin, lat=41.60, lng=69.70)
    owner = uuid.uuid4()
    for c in (near, far):
        api.post(f"/v1/admin/clinics/{c['id']}/members", json={"user_id": str(owner)}, headers=admin)
    ch = user_h(user_keys, owner)

    a_uid, a = approved_doctor(api, user_keys, admin, spec["id"])
    b_uid, b = approved_doctor(api, user_keys, admin, other_spec["id"], home=True)
    for clinic, doc, uid in ((near, a, a_uid), (far, b, b_uid)):
        aff = api.post(f"/v1/clinic/{clinic['id']}/doctors/invite", json={"doctor_id": doc["id"]}, headers=ch).json()
        api.post(f"/v1/doctor/clinic-invites/{aff['affiliation_id']}/accept", headers=user_h(user_keys, uid))
    api.patch(
        "/v1/doctor/profile",
        headers=user_h(user_keys, b_uid),
        json={"home_base_latitude": "41.30", "home_base_longitude": "69.25", "home_visit_radius_km": 5},
    )
    # Tasdiqlanmagan shifokor ro'yxatda va internetda ko'rinmaydi
    pending_uid = uuid.uuid4()
    api.post("/v1/doctor/onboarding/start", headers=user_h(user_keys, pending_uid))

    all_ids = {d["id"] for d in api.get("/v1/catalog/doctors").json()}
    assert all_ids == {a["id"], b["id"]}
    assert [d["id"] for d in api.get("/v1/catalog/doctors", params={"specialization": spec["id"]}).json()] == [a["id"]]
    assert [d["id"] for d in api.get("/v1/catalog/doctors", params={"clinic": far["id"]}).json()] == [b["id"]]
    assert [d["id"] for d in api.get("/v1/catalog/doctors", params={"q": "aliyev", "home_only": True}).json()] == [
        b["id"]
    ]
    near_rows = api.get("/v1/catalog/doctors", params={"lat": 41.311, "lng": 69.240, "radius": 5}).json()
    assert [d["id"] for d in near_rows] == [a["id"]] and near_rows[0]["distance_km"] < 1
    home = api.get("/v1/catalog/doctors", params={"lat": 41.31, "lng": 69.25, "home_only": True}).json()
    assert [d["id"] for d in home] == [b["id"]]
    assert api.get("/v1/catalog/doctors", params={"lat": 42.5, "lng": 69.25, "home_only": True}).json() == []
    assert api.get("/v1/catalog/doctors", params={"lat": 41.3}).status_code == 400
    assert api.get("/v1/catalog/doctors", params={"lat": 1, "lng": 1, "radius": 1}).json() == []

    detail = api.get(f"/v1/catalog/doctors/{a['id']}").json()
    assert (
        [c["id"] for c in detail["clinics"]] == [near["id"]] and detail["is_new"] is True and detail["rating"] is None
    )
    pending_doc = api.get("/v1/doctor/profile", headers=user_h(user_keys, pending_uid)).json()
    assert api.get(f"/v1/catalog/doctors/{pending_doc['id']}").status_code == 404
    assert api.get(f"/v1/catalog/doctors/{pending_doc['id']}/services").status_code == 404
    assert len(api.get("/v1/catalog/clinics", params={"city": "toshkent"}).json()) == 2
    assert len(api.get("/v1/catalog/specializations").json()) == 2

    # Internal: bookability va access
    bk = api.get(f"/internal/v1/doctors/{b['id']}/bookability", headers=INTERNAL).json()
    assert bk["bookable"] is True and bk["home_base"] == {"lat": 41.3, "lng": 69.25} and bk["home_visit_radius_km"] == 5
    assert api.get(f"/internal/v1/doctors/{uuid.uuid4()}/bookability", headers=INTERNAL).status_code == 404
    acc = api.get("/internal/v1/access", params={"user_id": str(a_uid), "doctor_id": a["id"]}, headers=INTERNAL).json()
    assert acc["allowed"] is True and acc["role"] == "doctor"
    acc = api.get(
        "/internal/v1/access", params={"user_id": str(owner), "clinic_id": near["id"]}, headers=INTERNAL
    ).json()
    assert acc == {"allowed": True, "role": "clinic_admin", "clinic_ids": sorted([near["id"], far["id"]])}
    acc = api.get("/internal/v1/access", params={"user_id": str(a_uid), "doctor_id": b["id"]}, headers=INTERNAL).json()
    assert acc["allowed"] is False
    assert api.get("/internal/v1/access", params={"user_id": str(a_uid)}, headers=INTERNAL).json()["allowed"] is False
    assert api.get(f"/internal/v1/clinics/{uuid.uuid4()}", headers=INTERNAL).status_code == 404
    assert api.get(f"/internal/v1/rooms/{uuid.uuid4()}", headers=INTERNAL).status_code == 404


@pytest.mark.integration
def test_rating_recalculated(api: TestClient, db, files, user_keys, admin_keys, run_handler) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin)
    clinic = make_clinic(api, admin)
    _, doctor = approved_doctor(api, user_keys, admin, spec["id"])
    t0 = datetime.now(UTC)
    data = sample_data(
        "rating.recalculated",
        doctor_id=doctor["id"],
        rating=4.756,
        reviews_count=12,
        clinic_id=clinic["id"],
        clinic_rating=4.5,
        clinic_reviews_count=7,
    )
    run_handler("rating.recalculated", data, occurred_at=t0)
    # Kechikkan eski hisob yangisini buzmaydi
    run_handler("rating.recalculated", {**data, "rating": 1.0, "reviews_count": 3}, occurred_at=t0 - timedelta(hours=1))
    detail = api.get(f"/v1/catalog/doctors/{doctor['id']}").json()
    assert detail["rating"] == "4.76" and detail["reviews_count"] == 12 and detail["is_new"] is False
    assert api.get(f"/v1/catalog/clinics/{clinic['id']}").json()["rating"] == "4.50"
    run_handler(
        "rating.recalculated",
        {**data, "rating": 4.0, "reviews_count": 3, "clinic_id": None},
        occurred_at=t0 + timedelta(minutes=1),
    )
    detail = api.get(f"/v1/catalog/doctors/{doctor['id']}").json()
    assert detail["rating"] is None and detail["is_new"] is True  # < 5 sharh — yashiriladi


def test_every_catalog_consume_has_handler() -> None:
    from medbron_common.eventbus import EventConsumer
    from medbron_common.events import CATALOG

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="catalog-service", amqp_url="amqp://x/", session_factory=None, processed_model=ProcessedEvent
    )
    handlers.register(consumer)
    assert consumer.routing_keys == sorted(e for e, s in CATALOG.items() if "catalog-service" in s.consumers)


@pytest.mark.integration
def test_specializations_admin(api: TestClient, admin_keys) -> None:
    admin = admin_h(admin_keys, "clinics.manage")
    spec = make_spec(api, admin)
    r = api.post("/v1/admin/specializations", json={"name": spec["name"], "slug": "other"}, headers=admin)
    assert r.status_code == 409
    r = api.patch(
        f"/v1/admin/specializations/{spec['id']}", json={"min_patient_age": 30, "max_patient_age": 10}, headers=admin
    )
    assert r.status_code == 400
    r = api.patch(
        f"/v1/admin/specializations/{spec['id']}",
        json={"allowed_gender": "female", "name_ru": "Терапевт"},
        headers=admin,
    )
    assert r.json()["allowed_gender"] == "female" and r.json()["name_ru"] == "Терапевт"
    assert api.patch(f"/v1/admin/specializations/{uuid.uuid4()}", json={}, headers=admin).status_code == 404
    assert len(api.get("/v1/admin/specializations", headers=admin).json()) == 1
