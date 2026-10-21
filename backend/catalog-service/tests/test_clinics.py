"""Klinikalar, a'zolar, xonalar, hamkorliklar va xizmatlar. Docker talab qiladi."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.helpers import INTERNAL, admin_h, approved_doctor, events, make_clinic, make_spec, user_h

pytestmark = pytest.mark.integration


def test_clinic_lifecycle_and_members(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys, "clinics.manage")
    clinic = make_clinic(api, admin, publish=False)
    assert clinic["status"] == "draft"
    assert api.get(f"/v1/catalog/clinics/{clinic['id']}").status_code == 404  # qoralama ommaga ko'rinmaydi
    r = api.post(f"/v1/admin/clinics/{clinic['id']}/suspend", json={"reason": "x"}, headers=admin)
    assert r.status_code == 409
    api.post(f"/v1/admin/clinics/{clinic['id']}/publish", headers=admin)
    assert api.get(f"/v1/catalog/clinics/{clinic['id']}").status_code == 200
    published = events(db, "clinic.published")
    assert published[0]["clinic_id"] == clinic["id"] and published[0]["working_hours"]["0"] == ["08:00", "18:00"]

    r = api.post(
        "/v1/admin/clinics",
        headers=admin,
        json={
            "name": "X",
            "city": "T",
            "street": "s",
            "latitude": "1",
            "longitude": "1",
            "working_hours": {"0": ["18:00", "08:00"]},
        },
    )
    assert r.status_code == 422  # ish vaqti noto'g'ri

    owner = uuid.uuid4()
    r = api.post(f"/v1/admin/clinics/{clinic['id']}/members", json={"user_id": str(owner)}, headers=admin)
    assert r.status_code == 201, r.text
    api.post(f"/v1/admin/clinics/{clinic['id']}/members", json={"user_id": str(owner)}, headers=admin)  # idempotent
    assert events(db, "clinic.member_added") == [{"clinic_id": clinic["id"], "user_id": str(owner)}]
    h = user_h(user_keys, owner)
    assert [c["id"] for c in api.get("/v1/clinic/mine", headers=h).json()] == [clinic["id"]]

    # Klinika admini: faqat moderatsiyasiz maydonlar
    assert api.patch(f"/v1/clinic/{clinic['id']}/profile", json={"name": "Yangi"}, headers=h).status_code == 422
    photo = uuid.uuid4()
    r = api.patch(
        f"/v1/clinic/{clinic['id']}/profile",
        headers=h,
        json={"phone": "+998712000000", "working_hours": {"1": ["09:00", "17:00"]}, "photo_file_id": str(photo)},
    )
    assert r.status_code == 200 and r.json()["photo_url"] and files.attached[-1][2] == "clinic_photo"
    assert events(db, "clinic.updated")[-1]["status"] == "active"
    # Begona klinika — xuddi yo'qdek
    other = make_clinic(api, admin)
    assert api.get(f"/v1/clinic/{other['id']}/profile", headers=h).status_code == 404

    room = api.post(f"/v1/clinic/{clinic['id']}/rooms", json={"name": "101", "equipment": ["UZI"]}, headers=h).json()
    assert api.post(f"/v1/clinic/{clinic['id']}/rooms", json={"name": "101"}, headers=h).status_code == 409
    r = api.patch(f"/v1/clinic/{clinic['id']}/rooms/{room['id']}", json={"is_active": False}, headers=h)
    assert r.json()["is_active"] is False
    assert len(api.get(f"/v1/clinic/{clinic['id']}/rooms", headers=h).json()) == 1
    assert api.get(f"/internal/v1/rooms/{room['id']}", headers=INTERNAL).json()["clinic_id"] == clinic["id"]

    r = api.patch(f"/v1/admin/clinics/{clinic['id']}", json={"name": "Shifo Plus", "latitude": "41.2"}, headers=admin)
    assert r.json()["name"] == "Shifo Plus"
    api.post(f"/v1/admin/clinics/{clinic['id']}/suspend", json={"reason": "Litsenziya"}, headers=admin)
    assert events(db, "clinic.suspended")[0]["reason"] == "Litsenziya"
    internal = api.get(f"/internal/v1/clinics/{clinic['id']}", headers=INTERNAL).json()
    assert internal["status"] == "suspended" and internal["name"] == "Shifo Plus"
    api.post(f"/v1/admin/clinics/{clinic['id']}/reinstate", headers=admin)
    assert len(events(db, "clinic.published")) == 3  # 2 ta publish + reinstate

    second = make_clinic(api, admin)
    api.post(f"/v1/admin/clinics/{second['id']}/members", json={"user_id": str(owner)}, headers=admin)
    assert len(api.get(f"/v1/admin/clinics/{second['id']}/members", headers=admin).json()) == 1
    api.request("DELETE", f"/v1/admin/clinics/{clinic['id']}/members/{owner}", headers=admin)
    api.request("DELETE", f"/v1/admin/clinics/{second['id']}/members/{owner}", headers=admin)
    assert [e["remaining_clinics"] for e in events(db, "clinic.member_removed")] == [1, 0]
    assert api.get(f"/v1/clinic/{clinic['id']}/profile", headers=h).status_code == 404
    assert len(api.get("/v1/admin/clinics", params={"status": "active"}, headers=admin).json()) == 3


def test_affiliation_flow(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin)
    clinic = make_clinic(api, admin)
    owner = uuid.uuid4()
    api.post(f"/v1/admin/clinics/{clinic['id']}/members", json={"user_id": str(owner)}, headers=admin)
    ch = user_h(user_keys, owner)
    doc_uid, doctor = approved_doctor(api, user_keys, admin, spec["id"])
    dh = user_h(user_keys, doc_uid)
    base = f"/v1/clinic/{clinic['id']}/doctors"

    assert api.post(f"{base}/invite", json={"doctor_id": str(uuid.uuid4())}, headers=ch).status_code == 404
    aff = api.post(f"{base}/invite", json={"doctor_id": doctor["id"], "position": "Terapevt"}, headers=ch).json()
    assert api.post(f"{base}/invite", json={"doctor_id": doctor["id"]}, headers=ch).status_code == 409
    invited = events(db, "affiliation.invited")[0]
    assert invited["doctor_user_id"] == str(doc_uid) and invited["clinic_name"] == clinic["name"]
    assert [i["affiliation_id"] for i in api.get("/v1/doctor/clinic-invites", headers=dh).json()] == [
        aff["affiliation_id"]
    ]

    # Boshqa shifokor bu taklifga javob bera olmaydi
    other_uid, _ = approved_doctor(api, user_keys, admin, spec["id"])
    r = api.post(f"/v1/doctor/clinic-invites/{aff['affiliation_id']}/accept", headers=user_h(user_keys, other_uid))
    assert r.status_code == 404
    assert (
        api.post(f"/v1/doctor/clinic-invites/{aff['affiliation_id']}/accept", headers=dh).json()["status"] == "active"
    )
    assert events(db, "affiliation.accepted")[0]["doctor_id"] == doctor["id"]
    assert api.get(f"/internal/v1/doctors/{doctor['id']}/bookability", headers=INTERNAL).json()["clinic_ids"] == [
        clinic["id"]
    ]

    act = f"{base}/{aff['affiliation_id']}"
    assert api.post(f"{act}/resume", headers=ch).status_code == 200  # allaqachon faol — idempotent
    assert api.post(f"{act}/pause", headers=ch).json()["status"] == "paused"
    assert events(db, "affiliation.paused")[0]["paused_by"] == str(owner)
    assert api.get(f"/internal/v1/doctors/{doctor['id']}/bookability", headers=INTERNAL).json()["clinic_ids"] == []
    api.post(f"{act}/resume", headers=ch)
    assert events(db, "affiliation.resumed")[0]["resumed_by"] == str(owner)
    assert api.post(f"{act}/bogus", headers=ch).status_code == 404
    assert len(api.get(base, headers=ch).json()) == 1
    assert api.post(f"/v1/doctor/affiliations/{aff['affiliation_id']}/leave", headers=dh).json()["status"] == "ended"
    ended = events(db, "affiliation.ended")
    assert ended == [{**ended[0], "ended_by": "doctor", "status": "left"}]

    # Qayta taklif — o'sha qator qayta ishlatiladi; rad etish
    again = api.post(f"{base}/invite", json={"doctor_id": doctor["id"]}, headers=ch).json()
    assert again["affiliation_id"] == aff["affiliation_id"]
    api.post(f"/v1/doctor/clinic-invites/{again['affiliation_id']}/decline", headers=dh)
    assert events(db, "affiliation.ended")[-1]["status"] == "declined"
    api.post(f"{base}/invite", json={"doctor_id": doctor["id"]}, headers=ch)
    api.post(f"/v1/doctor/clinic-invites/{again['affiliation_id']}/accept", headers=dh)
    assert len(api.get("/v1/doctor/affiliations", headers=dh).json()) == 1
    api.post(f"{act}/end", headers=ch)
    assert events(db, "affiliation.ended")[-1] == {**ended[0], "ended_by": "clinic", "status": "ended"}


def test_services_and_bookable_set(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin, accepts_children=True, min_patient_age=1)
    other_spec = make_spec(api, admin, name="Stomatolog")
    clinic = make_clinic(api, admin)
    owner = uuid.uuid4()
    api.post(f"/v1/admin/clinics/{clinic['id']}/members", json={"user_id": str(owner)}, headers=admin)
    ch = user_h(user_keys, owner)
    doc_uid, doctor = approved_doctor(api, user_keys, admin, spec["id"])
    dh = user_h(user_keys, doc_uid)

    body = {
        "specialization_id": spec["id"],
        "name": "Ko'rik",
        "place": "clinic",
        "duration_minutes": 30,
        "price_tiyin": 10_000_000,
    }
    assert (
        api.post("/v1/doctor/services", json={**body, "specialization_id": other_spec["id"]}, headers=dh).status_code
        == 400
    )  # shifokor mutaxassisligi emas
    assert api.post("/v1/doctor/services", json={**body, "place": "home"}, headers=dh).status_code == 400
    own = api.post("/v1/doctor/services", json=body, headers=dh).json()
    created = events(db, "service.created")[0]
    assert (
        created["price_tiyin"] == 10_000_000 and created["doctor_id"] == doctor["id"] and created["clinic_id"] is None
    )

    r = api.patch(f"/v1/doctor/services/{own['id']}", json={"price_tiyin": 20_000_000}, headers=dh)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "LARGE_PRICE_CHANGE"
    r = api.patch(
        f"/v1/doctor/services/{own['id']}", json={"price_tiyin": 20_000_000, "confirm_large_change": True}, headers=dh
    )
    assert r.json()["price_tiyin"] == 20_000_000
    upd = events(db, "service.updated")[0]
    assert upd["previous_price_tiyin"] == 10_000_000
    history = api.get(f"/v1/doctor/services/{own['id']}/price-history", headers=dh).json()
    assert history[0]["is_large_change"] is True

    clinic_svc = api.post(
        f"/v1/clinic/{clinic['id']}/services", json={**body, "name": "EKG", "price_tiyin": 5_000_000}, headers=ch
    ).json()
    assert clinic_svc["doctor_id"] is None
    assert (
        api.post(f"/v1/clinic/{clinic['id']}/services", json={**body, "place": "home"}, headers=ch).status_code == 400
    )
    # Shifokor klinika xizmatini o'zgartira olmaydi
    assert api.patch(f"/v1/doctor/services/{clinic_svc['id']}", json={"name": "XY"}, headers=dh).status_code == 404

    ids = f"{own['id']},{clinic_svc['id']}"
    q = {"ids": ids, "bookable_for": doctor["id"]}
    assert [s["id"] for s in api.get("/internal/v1/services", params=q, headers=INTERNAL).json()["items"]] == [
        own["id"]
    ]
    aff = api.post(f"/v1/clinic/{clinic['id']}/doctors/invite", json={"doctor_id": doctor["id"]}, headers=ch).json()
    api.post(f"/v1/doctor/clinic-invites/{aff['affiliation_id']}/accept", headers=dh)
    items = api.get("/internal/v1/services", params=q, headers=INTERNAL).json()["items"]
    assert {s["id"] for s in items} == {own["id"], clinic_svc["id"]}
    rules = items[0]["patient_rules"]
    assert rules["accepts_children"] is True and rules["min_patient_age"] == 1
    public = api.get(f"/v1/catalog/doctors/{doctor['id']}/services", params={"place": "clinic"}).json()
    assert [s["price_tiyin"] for s in public] == [5_000_000, 20_000_000]  # narx bo'yicha

    r = api.request("DELETE", f"/v1/doctor/services/{own['id']}", headers=dh)
    assert r.json()["is_active"] is False
    api.request("DELETE", f"/v1/doctor/services/{own['id']}", headers=dh)  # takror — event yo'q
    assert len(events(db, "service.deactivated")) == 1
    assert api.patch(f"/v1/doctor/services/{own['id']}", json={"is_active": True}, headers=dh).json()["is_active"]
    api.request("DELETE", f"/v1/clinic/{clinic['id']}/services/{clinic_svc['id']}", headers=ch)
    assert len(api.get(f"/v1/clinic/{clinic['id']}/services", headers=ch).json()) == 1
    assert len(api.get("/v1/doctor/services", headers=dh).json()) == 1
    r = api.get("/internal/v1/services", params={"ids": "not-a-uuid"}, headers=INTERNAL)
    assert r.status_code == 400
