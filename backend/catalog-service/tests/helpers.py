"""Testlar uchun umumiy yordamchilar: tokenlar, outbox o'qish, tayyor ma'lumot (spec, klinika, shifokor)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient

from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token

INTERNAL = {"X-Caller": "booking-service", "X-Internal-Token": "test-token"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def user_h(keys: Any, user_id: uuid.UUID) -> dict[str, str]:
    return bearer(mint_user_token(keys, user_id=user_id))


def admin_h(keys: Any, *perms: str) -> dict[str, str]:
    return bearer(mint_admin_token(keys, permissions=list(perms or ("*",))))


def events(db: Any, event_type: str) -> list[dict[str, Any]]:
    rows = db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
    for (payload,) in rows:
        assert_event_valid(payload)
    return [row[0]["data"] for row in rows]


def make_spec(api: TestClient, admin: dict[str, str], name: str = "Terapevt", **extra: Any) -> dict[str, Any]:
    slug = name.lower().replace(" ", "-") + "-" + uuid.uuid4().hex[:4]
    r = api.post(
        "/v1/admin/specializations", json={"name": f"{name} {slug[-4:]}", "slug": slug, **extra}, headers=admin
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


def make_clinic(
    api: TestClient, admin: dict[str, str], *, publish: bool = True, lat: float = 41.311081, lng: float = 69.240562
) -> dict[str, Any]:
    body = {
        "name": f"Shifo {uuid.uuid4().hex[:6]}",
        "city": "Toshkent",
        "street": "Amir Temur 1",
        "latitude": str(lat),
        "longitude": str(lng),
        "working_hours": {"0": ["08:00", "18:00"], "6": None},
    }
    r = api.post("/v1/admin/clinics", json=body, headers=admin)
    assert r.status_code == 201, r.text
    clinic = dict(r.json())
    if publish:
        assert api.post(f"/v1/admin/clinics/{clinic['id']}/publish", headers=admin).status_code == 200
    return clinic


def approved_doctor(
    api: TestClient, user_keys: Any, admin: dict[str, str], spec_id: str, *, home: bool = False
) -> tuple[uuid.UUID, dict[str, Any]]:
    """Onboarding → hujjatlar → submit → approve. Qaytaradi: (user_id, doctor)."""
    uid = uuid.uuid4()
    h = user_h(user_keys, uid)
    assert api.post("/v1/doctor/onboarding/start", headers=h).status_code == 201
    profile = {
        "full_name": "Dr. Aliyev",
        "experience_years": 10,
        "languages": ["uz", "ru"],
        "license_number": "L-1",
        "license_expires_at": (date.today() + timedelta(days=365)).isoformat(),
        "specialization_ids": [spec_id],
        "accepts_home_visits": home,
    }
    assert api.patch("/v1/doctor/onboarding/profile", json=profile, headers=h).status_code == 200
    for kind in ("diploma", "license"):
        r = api.post("/v1/doctor/onboarding/documents", json={"kind": kind, "file_id": str(uuid.uuid4())}, headers=h)
        assert r.status_code == 201, r.text
    assert api.post("/v1/doctor/onboarding/submit", headers=h).status_code == 200
    doctor = api.get("/v1/doctor/profile", headers=h).json()
    r = api.post(f"/v1/admin/moderation/doctors/{doctor['id']}/approve", headers=admin)
    assert r.status_code == 200, r.text
    return uid, dict(r.json())
