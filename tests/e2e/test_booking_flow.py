"""3-bosqich e2e: asosiy domen (catalog → schedule → pricing → booking) Kong orqali, eventlar RabbitMQ'da.

Bitta "dunyo" (modul fixture'i) quriladi: admin mutaxassislik va klinika yaratadi, klinika admini tayinlanadi,
shifokor onboarding'dan o'tadi (hujjatlar file-service'da) va tasdiqlanadi, klinikaga taklif qilinadi,
xizmatlar va ish qoidalari yaratiladi. Testlar shu dunyoda mijoz bronlarini sinaydi.

Klinika bronlari — `at_clinic` (darhol tasdiqlanadi); uy chaqiruvi — har doim prepaid: `payment.captured`
eventini payment-service o'rniga booking konteyneri ichidan (o'z RabbitMQ sozlamalari bilan) yuboramiz.
"""

from __future__ import annotations

import base64
import json
import secrets
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import bearer, eventually

ROOT = Path(__file__).resolve().parents[2]
TASHKENT = ZoneInfo("Asia/Tashkent")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
# Klinika va uy bazasi — Toshkent markazi; mijoz manzili ~2 km
CLINIC_POINT = (41.311081, 69.240562)
CLIENT_POINT = (41.326000, 69.228000)


def ok(r: httpx.Response, *codes: int) -> Any:
    assert r.status_code in (codes or (200,)), f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text}"
    return r.json() if r.content else None


def claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    return dict(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))))


def day(offset: int) -> date:
    return datetime.now(TASHKENT).date() + timedelta(days=offset)


def publish_event(event_type: str, aggregate_type: str, aggregate_id: str, data: dict[str, Any]) -> None:
    """Hali ko'chirilmagan servis (payment) eventini booking-service konteyneri ichidan yuboradi —
    test kodiga RabbitMQ paroli tushmaydi, envelope `medbron_common` bilan quriladi."""
    script = (
        "import asyncio, json, sys\n"
        "from medbron_common.eventbus.envelope import build_envelope\n"
        "from medbron_common.eventbus.publisher import EventPublisher\n"
        "from app.core.config import get_settings\n"
        "async def main():\n"
        "    pub = EventPublisher(get_settings().rabbitmq_url.get_secret_value())\n"
        "    await pub.connect()\n"
        "    await pub.publish(build_envelope(**json.loads(sys.argv[1])))\n"
        "    await pub.close()\n"
        "asyncio.run(main())\n"
    )
    envelope = {
        "event_type": event_type,
        "producer": "payment-service",
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "data": data,
    }
    subprocess.run(  # noqa: S603
        ["docker", "compose", "exec", "-T", "booking-service", "python", "-", json.dumps(envelope)],  # noqa: S607
        cwd=ROOT / "backend" / "booking-service",
        input=script.encode(),
        check=True,
        capture_output=True,
        timeout=60,
    )


def upload_document(http: httpx.Client, h: dict[str, str]) -> str:
    ticket = ok(http.post("/v1/files/uploads", headers=h,
                          json={"purpose": "doctor_document", "content_type": "application/pdf", "size": len(PDF)}),
                201, 200)  # fmt: skip
    put = httpx.put(ticket["upload_url"], content=PDF, headers=ticket["headers"])
    assert put.status_code == 200, put.text
    ok(http.post(f"/v1/files/{ticket['file_id']}/complete", headers=h))
    return str(ticket["file_id"])


def free_slots(http: httpx.Client, doctor_id: str, on: date, place: str) -> list[dict[str, Any]]:
    slots = ok(http.get(f"/v1/schedule/doctors/{doctor_id}/slots", params={"date": on.isoformat()}))
    return [s for s in slots if s["place"] == place]


@pytest.fixture(scope="module")
def world(http: httpx.Client, login, admin_token: str) -> dict[str, Any]:
    ah = bearer(admin_token)
    tag = secrets.token_hex(3)

    spec = ok(http.post("/v1/admin/specializations", headers=ah,
                        json={"name": f"Terapevt {tag}", "slug": f"e2e-terapevt-{tag}"}), 201)  # fmt: skip
    clinic = ok(http.post("/v1/admin/clinics", headers=ah, json={
        "name": f"E2E klinika {tag}", "city": "Toshkent", "street": "Amir Temur 1",
        "latitude": str(CLINIC_POINT[0]), "longitude": str(CLINIC_POINT[1]),
    }), 201)  # fmt: skip
    ok(http.post(f"/v1/admin/clinics/{clinic['id']}/publish", headers=ah))

    clinic_admin = login()
    ok(http.post(f"/v1/admin/clinics/{clinic['id']}/members", headers=ah,
                 json={"user_id": clinic_admin["user"]["id"]}), 201)  # fmt: skip
    ch = bearer(clinic_admin["access"])

    # --- shifokor onboarding → moderatsiya ---------------------------------------------------------
    doctor = login()
    dh = bearer(doctor["access"])
    profile = ok(http.post("/v1/doctor/onboarding/start", headers=dh), 201, 200)
    doctor_id = profile["id"]
    ok(http.patch("/v1/doctor/onboarding/profile", headers=dh, json={
        "full_name": f"E2E Shifokor {tag}", "experience_years": 7, "languages": ["uz", "ru"],
        "license_number": f"LIC-{tag}", "license_expires_at": day(365).isoformat(),
        "accepts_home_visits": True, "home_visit_radius_km": 15, "specialization_ids": [spec["id"]],
    }))  # fmt: skip
    for kind in ("diploma", "license"):
        ok(http.post("/v1/doctor/onboarding/documents", headers=dh,
                     json={"kind": kind, "file_id": upload_document(http, dh)}), 201)  # fmt: skip
    submitted = ok(http.post("/v1/doctor/onboarding/submit", headers=dh))
    assert submitted["status"] == "pending" and submitted["missing"] == [], submitted
    ok(http.post(f"/v1/admin/moderation/doctors/{doctor_id}/approve", headers=ah))

    # doctor.approved → auth: yangi access token'da `doctor_id` claim
    refresh = {"token": doctor["refresh"]}

    def doctor_claim() -> str:
        r = ok(http.post("/v1/auth/token/refresh", json={"refresh": refresh["token"]}))
        refresh["token"] = r["refresh"]
        assert claims(r["access"]).get("doctor_id") == doctor_id
        return str(r["access"])

    dh = bearer(eventually(doctor_claim))
    ok(http.patch("/v1/doctor/profile", headers=dh, json={
        "clinic_payment_mode": "at_clinic",
        "home_base_latitude": str(CLINIC_POINT[0]), "home_base_longitude": str(CLINIC_POINT[1]),
    }))  # fmt: skip

    # --- klinika ↔ shifokor -----------------------------------------------------------------------
    aff = ok(http.post(f"/v1/clinic/{clinic['id']}/doctors/invite", headers=ch, json={"doctor_id": doctor_id}), 201)
    ok(http.post(f"/v1/doctor/clinic-invites/{aff['affiliation_id']}/accept", headers=dh))

    clinic_service = ok(http.post("/v1/doctor/services", headers=dh, json={
        "specialization_id": spec["id"], "name": "Konsultatsiya", "place": "clinic",
        "duration_minutes": 30, "price_tiyin": 150_000_00,
    }), 201)  # fmt: skip
    home_service = ok(http.post("/v1/doctor/services", headers=dh, json={
        "specialization_id": spec["id"], "name": "Uyga chaqiruv", "place": "home",
        "duration_minutes": 60, "price_tiyin": 300_000_00,
    }), 201)  # fmt: skip

    # --- ish qoidalari: schedule proyeksiyasi (approved, affiliation, uy chaqiruvi) event bilan keladi ---
    def create_rule(body: dict[str, Any]) -> None:
        r = http.post("/v1/doctor/schedule/rules", headers=dh, json=body)
        assert r.status_code == 201, r.text

    for weekday in range(7):
        common = {"weekday": weekday, "slot_minutes": 30, "valid_from": day(0).isoformat()}
        eventually(lambda c=common: create_rule({**c, "clinic_id": clinic["id"],
                                                  "start_time": "09:00", "end_time": "13:00"}))  # fmt: skip
        eventually(lambda c=common: create_rule({**c, "clinic_id": None, "slot_minutes": 60,
                                                  "start_time": "15:00", "end_time": "19:00"}))  # fmt: skip

    return {
        "admin": ah, "clinic_admin": ch, "doctor": dh, "doctor_id": doctor_id, "clinic_id": clinic["id"],
        "clinic_service": clinic_service["id"], "home_service": home_service["id"], "spec": spec,
    }


@pytest.fixture
def client(http: httpx.Client, login) -> dict[str, Any]:
    session = login()
    h = bearer(session["access"])
    patient = ok(http.post("/v1/users/me/patients", headers=h,
                           json={"full_name": "E2E Bemor", "birth_date": "1990-05-01", "gender": "male"}), 201)  # fmt: skip
    return {"h": h, "id": session["user"]["id"], "patient_id": patient["id"]}


def book(http: httpx.Client, client: dict[str, Any], world: dict[str, Any], slot: dict[str, Any],
         service: str, key: str | None = None, **extra: Any) -> httpx.Response:  # fmt: skip
    body = {"patient_id": client["patient_id"], "doctor_id": world["doctor_id"], "slot_id": slot["id"],
            "service_ids": [world[service]], **extra}  # fmt: skip
    return http.post("/v1/bookings", headers={**client["h"], "Idempotency-Key": key or str(uuid.uuid4())}, json=body)


def test_public_catalog_shows_approved_doctor_and_slots(http: httpx.Client, world: dict[str, Any]) -> None:
    doc = ok(http.get(f"/v1/catalog/doctors/{world['doctor_id']}"))
    assert doc["id"] == world["doctor_id"]
    services = ok(http.get(f"/v1/catalog/doctors/{world['doctor_id']}/services"))
    assert {s["id"] for s in services} >= {world["clinic_service"], world["home_service"]}

    def has_slots() -> None:
        assert free_slots(http, world["doctor_id"], day(2), "clinic")
        assert free_slots(http, world["doctor_id"], day(2), "home")

    eventually(has_slots)
    # /internal Kong'da yopiq
    assert http.get(f"/internal/v1/doctors/{world['doctor_id']}/bookability").status_code == 404


def test_clinic_booking_confirms_immediately_and_cancel_frees_slot(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any]
) -> None:
    slot = eventually(lambda: free_slots(http, world["doctor_id"], day(3), "clinic")[0])
    preview = ok(http.post("/v1/pricing/quote", headers=client["h"], json={
        "doctor_id": world["doctor_id"], "service_ids": [world["clinic_service"]], "place": "clinic"}))  # fmt: skip
    assert preview["payment_mode"] == "at_clinic" and preview["prepay_tiyin"] == 0

    key = str(uuid.uuid4())
    booking = ok(book(http, client, world, slot, "clinic_service", key), 201)
    assert booking["status"] == "confirmed" and booking["number"].startswith("MB-")
    assert booking["total_tiyin"] == preview["total_tiyin"] and booking["clinic_id"] == world["clinic_id"]
    # Takror (o'sha Idempotency-Key) — o'sha bron, 200
    replay = ok(book(http, client, world, slot, "clinic_service", key), 200)
    assert replay["id"] == booking["id"]

    # Slot band — ommaviy ro'yxatdan yo'qoladi (kesh event bilan tozalanadi, ≤ 30 s)
    def slot_gone() -> None:
        assert slot["id"] not in {s["id"] for s in free_slots(http, world["doctor_id"], day(3), "clinic")}

    eventually(slot_gone, timeout=45)

    # Shifokor va klinika ko'rinishi
    mine = ok(http.get("/v1/doctor/appointments", headers=world["doctor"],
                       params={"from": day(3).isoformat(), "to": day(4).isoformat()}))  # fmt: skip
    items = mine["items"] if isinstance(mine, dict) else mine
    assert booking["id"] in {a["id"] for a in items}
    clinic_day = ok(http.get(f"/v1/clinic/{world['clinic_id']}/appointments", headers=world["clinic_admin"],
                             params={"date": day(3).isoformat()}))  # fmt: skip
    clinic_items = clinic_day["items"] if isinstance(clinic_day, dict) else clinic_day
    assert booking["id"] in {a["id"] for a in clinic_items}

    # Bekor qilish (> 24 soat — to'liq) → slot qaytadan bo'sh
    cp = ok(http.get(f"/v1/bookings/{booking['id']}/cancellation-preview", headers=client["h"]))
    assert cp["allowed"] is True
    cancelled = ok(http.post(f"/v1/bookings/{booking['id']}/cancel", headers=client["h"], json={"reason": "e2e"}))
    assert cancelled["status"] == "cancelled"

    def slot_back() -> None:
        assert slot["id"] in {s["id"] for s in free_slots(http, world["doctor_id"], day(3), "clinic")}

    eventually(slot_back, timeout=45)


def test_parallel_bookings_one_slot_only_one_wins(
    http: httpx.Client, login, world: dict[str, Any], client: dict[str, Any]
) -> None:
    other = login()
    oh = bearer(other["access"])
    other_patient = ok(http.post("/v1/users/me/patients", headers=oh,
                                 json={"full_name": "Ikkinchi", "birth_date": "1985-01-01", "gender": "female"}), 201)  # fmt: skip
    rival = {"h": oh, "id": other["user"]["id"], "patient_id": other_patient["id"]}
    slot = eventually(lambda: free_slots(http, world["doctor_id"], day(4), "clinic")[0])

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda c: book(http, c, world, slot, "clinic_service"), (client, rival)))
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 409).json()["detail"]
    assert loser["code"] == "SLOT_TAKEN"


def test_home_visit_prepaid_confirmed_by_payment_event_then_rescheduled(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any]
) -> None:
    address = ok(http.post("/v1/users/me/addresses", headers=client["h"], json={
        "city": "Toshkent", "street": "Navoiy 10", "apartment": "12",
        "latitude": str(CLIENT_POINT[0]), "longitude": str(CLIENT_POINT[1]),
    }), 201)  # fmt: skip
    slots = eventually(lambda: free_slots(http, world["doctor_id"], day(5), "home"))
    assert len(slots) >= 3, slots

    booking = ok(book(http, client, world, slots[0], "home_service", address_id=address["id"]), 201)
    assert booking["status"] == "pending_payment" and booking["payment_mode"] == "prepaid"
    assert booking["prepay_tiyin"] == booking["total_tiyin"] > 0 and booking["payment_due_at"]
    assert booking["address"]["street"] == "Navoiy 10"

    # Yo'l buferi: qo'shni uy sloti ham yopiladi (60 daq slot + bufer)
    def neighbour_blocked() -> None:
        free = {s["id"] for s in free_slots(http, world["doctor_id"], day(5), "home")}
        assert slots[0]["id"] not in free and slots[1]["id"] not in free

    eventually(neighbour_blocked, timeout=45)

    publish_event("payment.captured", "payment", str(uuid.uuid4()), {
        "payment_id": str(uuid.uuid4()), "booking_id": booking["id"], "client_id": client["id"],
        "provider": "payme", "amount_tiyin": booking["prepay_tiyin"],
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    })  # fmt: skip

    def confirmed() -> dict[str, Any]:
        b = ok(http.get(f"/v1/bookings/{booking['id']}", headers=client["h"]))
        assert b["status"] == "confirmed" and b["paid_tiyin"] == booking["prepay_tiyin"], b
        return dict(b)

    eventually(confirmed)

    # Ko'chirish (≥ 24 soat oldin): narx o'zgarmaydi, eski slot bo'shaydi
    target = eventually(lambda: free_slots(http, world["doctor_id"], day(6), "home")[0])
    moved = ok(http.post(f"/v1/bookings/{booking['id']}/reschedule", headers=client["h"],
                         json={"new_slot_id": target["id"]}))  # fmt: skip
    assert moved["start_at"] != booking["start_at"] and moved["total_tiyin"] == booking["total_tiyin"]
    assert moved["rescheduled_count"] == 1 and moved["status"] == "confirmed"

    def old_slot_free() -> None:
        assert slots[0]["id"] in {s["id"] for s in free_slots(http, world["doctor_id"], day(5), "home")}

    eventually(old_slot_free, timeout=45)


def test_foreign_booking_is_404_and_doctor_endpoints_need_doctor(
    http: httpx.Client, login, world: dict[str, Any], client: dict[str, Any]
) -> None:
    slot = eventually(lambda: free_slots(http, world["doctor_id"], day(7), "clinic")[0])
    booking = ok(book(http, client, world, slot, "clinic_service"), 201)
    stranger = bearer(login()["access"])
    assert http.get(f"/v1/bookings/{booking['id']}", headers=stranger).status_code == 404
    assert http.get("/v1/doctor/appointments/today", headers=stranger).status_code == 403
    assert http.get(f"/v1/clinic/{world['clinic_id']}/appointments", headers=stranger).status_code in (403, 404)
