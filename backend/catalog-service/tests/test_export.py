"""search-service reindex eksporti: keyset sahifalash, faqat ommaviy maydonlar, chaqiruvchi cheklovi (Docker)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.helpers import admin_h, approved_doctor, make_clinic, make_spec, user_h

pytestmark = pytest.mark.integration

SEARCH = {"X-Caller": "search-service", "X-Internal-Token": "test-token"}


def test_export_doctors_clinics_specializations(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin)
    clinic = make_clinic(api, admin)
    make_clinic(api, admin, publish=False)  # qoralama — eksportga kirmaydi
    owner = uuid.uuid4()
    api.post(f"/v1/admin/clinics/{clinic['id']}/members", json={"user_id": str(owner)}, headers=admin)
    ch = user_h(user_keys, owner)
    doctors = [approved_doctor(api, user_keys, admin, spec["id"]) for _ in range(3)]
    doc_uid, doctor = doctors[0]
    dh = user_h(user_keys, doc_uid)
    aff = api.post(f"/v1/clinic/{clinic['id']}/doctors/invite", json={"doctor_id": doctor["id"]}, headers=ch).json()
    api.post(f"/v1/doctor/clinic-invites/{aff['affiliation_id']}/accept", headers=dh)
    body = {"specialization_id": spec["id"], "name": "Ko'rik", "place": "clinic", "duration_minutes": 30}
    own = api.post("/v1/doctor/services", json={**body, "price_tiyin": 10_000_000}, headers=dh).json()
    gone = api.post("/v1/doctor/services", json={**body, "name": "Eski", "price_tiyin": 1}, headers=dh).json()
    api.delete(f"/v1/doctor/services/{gone['id']}", headers=dh)
    api.post(f"/v1/clinic/{clinic['id']}/services", json={**body, "name": "EKG", "price_tiyin": 5_000_000}, headers=ch)
    # Moderatsiyadagi (pending) shifokor — eksportga kirmaydi
    pending = uuid.uuid4()
    api.post("/v1/doctor/onboarding/start", headers=user_h(user_keys, pending))

    seen: list[dict] = []
    after = None
    while True:
        params = {"limit": 2, **({"after": after} if after else {})}
        page = api.get("/internal/v1/export/doctors", params=params, headers=SEARCH).json()
        seen += page["items"]
        after = page["next_after"]
        if after is None:
            break
    assert [d["doctor_id"] for d in seen] == sorted(d["doctor_id"] for d in seen)  # keyset, takrorsiz
    assert {d["doctor_id"] for d in seen} == {d["id"] for _, d in doctors}
    mine = next(d for d in seen if d["doctor_id"] == doctor["id"])
    assert mine["status"] == "approved" and mine["specialization_ids"] == [spec["id"]]
    assert [a["clinic_id"] for a in mine["affiliations"]] == [clinic["id"]]
    assert [s["id"] for s in mine["services"]] == [own["id"]]  # nofaol xizmat chiqmaydi
    assert "license_number" not in mine and "status_reason" not in mine  # faqat ommaviy maydonlar

    clinics = api.get("/internal/v1/export/clinics", headers=SEARCH).json()
    assert [c["clinic_id"] for c in clinics["items"]] == [clinic["id"]] and clinics["next_after"] is None
    assert [s["name"] for s in clinics["items"][0]["services"]] == ["EKG"]
    specs = api.get("/internal/v1/export/specializations", headers=SEARCH).json()["items"]
    assert [s["slug"] for s in specs] == [spec["slug"]]

    assert api.get("/internal/v1/export/doctors", params={"limit": 501}, headers=SEARCH).status_code == 422
    other = {"X-Caller": "review-service", "X-Internal-Token": "test-token"}
    assert api.get("/internal/v1/export/doctors", headers=other).status_code in (401, 403)
