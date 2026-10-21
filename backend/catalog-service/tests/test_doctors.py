"""Shifokor: onboarding, moderatsiya holat mashinasi, profil, litsenziya va kunlik tekshiruv. Docker talab qiladi."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.helpers import admin_h, approved_doctor, events, make_spec, user_h

pytestmark = pytest.mark.integration


def test_onboarding_submit_requirements_and_moderation(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin)
    uid = uuid.uuid4()
    h = user_h(user_keys, uid)
    assert api.get("/v1/doctor/profile", headers=h).json()["detail"]["code"] == "NOT_A_DOCTOR"
    assert api.post("/v1/doctor/onboarding/start", headers=h).status_code == 201
    assert api.post("/v1/doctor/onboarding/start", headers=h).status_code == 200  # idempotent

    status = api.get("/v1/doctor/onboarding/status", headers=h).json()
    assert set(status["missing"]) == {
        "full_name",
        "specialization_ids",
        "license_number",
        "license_expires_at",
        "document:diploma",
        "document:license",
    }
    r = api.post("/v1/doctor/onboarding/submit", headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "ONBOARDING_INCOMPLETE"

    r = api.patch("/v1/doctor/onboarding/profile", json={"specialization_ids": [str(uuid.uuid4())]}, headers=h)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "SPECIALIZATION_NOT_FOUND"
    expired = (date.today() - timedelta(days=1)).isoformat()
    api.patch(
        "/v1/doctor/onboarding/profile",
        headers=h,
        json={
            "full_name": "Dr. Karimov",
            "license_number": "L-77",
            "license_expires_at": expired,
            "specialization_ids": [spec["id"]],
            "languages": ["uz"],
        },
    )
    assert "license_expires_at:expired" in api.get("/v1/doctor/onboarding/status", headers=h).json()["missing"]
    api.patch(
        "/v1/doctor/onboarding/profile",
        json={"license_expires_at": (date.today() + timedelta(days=200)).isoformat()},
        headers=h,
    )

    files.reject.add(bad := uuid.uuid4())
    r = api.post("/v1/doctor/onboarding/documents", json={"kind": "diploma", "file_id": str(bad)}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_FILE"
    docs = []
    for kind in ("diploma", "license", "certificate"):
        r = api.post("/v1/doctor/onboarding/documents", json={"kind": kind, "file_id": str(uuid.uuid4())}, headers=h)
        docs.append(r.json())
    assert files.attached[0][2] == "doctor_document" and files.attached[0][1].startswith("doctor:")
    assert api.delete(f"/v1/doctor/onboarding/documents/{docs[2]['id']}", headers=h).status_code == 204
    assert files.released[-1][0] == uuid.UUID(docs[2]["file_id"])
    assert len(api.get("/v1/doctor/onboarding/documents", headers=h).json()) == 2

    r = api.post("/v1/doctor/onboarding/submit", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["sla_due_at"]
    submitted = events(db, "doctor.onboarding_submitted")
    assert submitted[0]["user_id"] == str(uid)
    # pending — tahrirlab bo'lmaydi
    r = api.patch("/v1/doctor/onboarding/profile", json={"bio": "x"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PROFILE_LOCKED"

    doctor_id = api.get("/v1/doctor/profile", headers=h).json()["id"]
    mod = admin_h(admin_keys, "doctors.moderate")
    queue = api.get("/v1/admin/moderation/doctors", headers=mod).json()
    assert [d["id"] for d in queue] == [doctor_id] and len(queue[0]["documents"]) == 2
    url = api.get(f"/v1/admin/moderation/doctors/{doctor_id}/documents/{docs[0]['id']}/url", headers=mod).json()
    assert url["expires_in"] == 300 and "viewer=admin" in url["url"]
    assert api.get("/v1/admin/moderation/doctors", headers=admin_h(admin_keys, "clinics.manage")).status_code == 403

    r = api.post(f"/v1/admin/moderation/doctors/{doctor_id}/reject", json={"reason": ""}, headers=mod)
    assert r.status_code == 400
    r = api.post(f"/v1/admin/moderation/doctors/{doctor_id}/reject", json={"reason": "Diplom o'qilmaydi"}, headers=mod)
    assert r.json()["status"] == "rejected" and r.json()["status_reason"] == "Diplom o'qilmaydi"
    assert events(db, "doctor.rejected")[0]["reason"] == "Diplom o'qilmaydi"
    # Rad etilgan — tuzatib qayta yuboradi
    assert api.patch("/v1/doctor/onboarding/profile", json={"bio": "Tajribali"}, headers=h).status_code == 200
    assert api.post("/v1/doctor/onboarding/submit", headers=h).json()["status"] == "pending"
    assert (
        api.post(f"/v1/admin/moderation/doctors/{doctor_id}/suspend", json={"reason": "x"}, headers=mod).status_code
        == 409
    )  # pending → suspend mumkin emas

    r = api.post(f"/v1/admin/moderation/doctors/{doctor_id}/approve", headers=mod)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    api.post(f"/v1/admin/moderation/doctors/{doctor_id}/approve", headers=mod)  # idempotent — ikkinchi event yo'q
    approved = events(db, "doctor.approved")
    assert len(approved) == 1 and approved[0]["specialization_ids"] == [spec["id"]]
    assert approved[0]["full_name"] == "Dr. Karimov"

    api.post(f"/v1/admin/moderation/doctors/{doctor_id}/suspend", json={"reason": "Shikoyat"}, headers=mod)
    assert events(db, "doctor.suspended")[0]["reason"] == "Shikoyat"
    # To'xtatilgan shifokor xizmat yarata olmaydi
    r = api.post(
        "/v1/doctor/services",
        headers=h,
        json={
            "specialization_id": spec["id"],
            "name": "Ko'rik",
            "place": "clinic",
            "duration_minutes": 30,
            "price_tiyin": 10,
        },
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "DOCTOR_NOT_APPROVED"
    api.post(f"/v1/admin/moderation/doctors/{doctor_id}/reinstate", headers=mod)
    assert events(db, "doctor.reinstated")[0]["doctor_id"] == doctor_id
    actions = [e["action"] for e in events(db, "audit.recorded")]
    assert {"doctor.status.rejected", "doctor.status.approved", "doctor.status.suspended"} <= set(actions)


def test_public_profile_photo_and_license(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin)
    uid, doctor = approved_doctor(api, user_keys, admin, spec["id"])
    h = user_h(user_keys, uid)
    photo = uuid.uuid4()
    body = {
        "follow_up_discount_percent": 30,
        "clinic_payment_mode": "deposit",
        "home_base_latitude": "41.3",
        "home_base_longitude": "69.2",
        "photo_file_id": str(photo),
        "accepts_home_visits": True,
    }
    r = api.patch("/v1/doctor/profile", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["clinic_payment_mode"] == "deposit" and r.json()["photo_url"].endswith(f"{photo}.jpg")
    assert files.attached[-1][2] == "doctor_photo"
    updated = events(db, "doctor.profile_updated")
    assert updated[-1]["photo_file_id"] == str(photo) and updated[-1]["accepts_home_visits"] is True
    r = api.patch("/v1/doctor/profile", json={"home_base_latitude": "41.0"}, headers=h)
    assert r.status_code == 400
    assert api.patch("/v1/doctor/profile", json={"full_name": "x"}, headers=h).status_code == 422  # moderatsiyasiz emas
    second = uuid.uuid4()
    api.patch("/v1/doctor/profile", json={"photo_file_id": str(second)}, headers=h)
    assert files.released[-1] == (photo, f"doctor:{doctor['id']}:photo")

    # ADR-0011 #8: to'xtatilgan shifokor litsenziyani yangilay oladi
    mod = admin_h(admin_keys, "doctors.moderate")
    api.post(f"/v1/admin/moderation/doctors/{doctor['id']}/suspend", json={"reason": "license_expired"}, headers=mod)
    r = api.put(
        "/v1/doctor/license", json={"license_number": "L-2", "license_expires_at": str(date.today())}, headers=h
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "LICENSE_EXPIRED"
    future = (date.today() + timedelta(days=700)).isoformat()
    r = api.put("/v1/doctor/license", json={"license_number": "L-2", "license_expires_at": future}, headers=h)
    assert r.status_code == 200 and r.json()["license_number"] == "L-2" and r.json()["status"] == "suspended"
    assert (
        api.post(f"/v1/admin/moderation/doctors/{doctor['id']}/reinstate", headers=mod).json()["status"] == "approved"
    )


def test_license_check_job(api: TestClient, db, files, user_keys, admin_keys) -> None:
    admin = admin_h(admin_keys)
    spec = make_spec(api, admin)
    _, expiring = approved_doctor(api, user_keys, admin, spec["id"])
    _, expired = approved_doctor(api, user_keys, admin, spec["id"])
    db.scalar("UPDATE doctors SET license_expires_at = CURRENT_DATE + 10 WHERE id = :i", i=expiring["id"])
    db.scalar("UPDATE doctors SET license_expires_at = CURRENT_DATE - 1 WHERE id = :i", i=expired["id"])

    from medbron_common.db import transaction

    from app.services.doctors import run_license_check

    async def run(session):
        async with transaction(session):
            return await run_license_check(session)

    assert db.run(run) == {"suspended": 1, "reminded": 1}
    assert db.run(run) == {"suspended": 0, "reminded": 0}  # eslatma bir marta
    assert events(db, "doctor.suspended")[0]["reason"] == "license_expired"
    reminder = events(db, "doctor.license_expiring")
    assert [r["doctor_id"] for r in reminder] == [expiring["id"]]
    assert db.scalar("SELECT status FROM doctors WHERE id = :i", i=expired["id"]) == "suspended"


def test_scheduler_entrypoint(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import reset_caches

    monkeypatch.setenv("DATABASE_URL", db.url.replace("+asyncpg", ""))
    reset_caches()
    from app.core.database import get_engine
    from app.workers.scheduler import license_check

    async def go() -> dict[str, int]:
        try:
            return await license_check()
        finally:
            await get_engine().dispose()

    assert asyncio.run(go()) == {"suspended": 0, "reminded": 0}
    reset_caches()
