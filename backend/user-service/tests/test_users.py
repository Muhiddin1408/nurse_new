"""Profil, bemorlar, manzillar, sevimlilar, o'chirish va internal API — haqiqiy Postgres + Redis bilan.
file-service soxta client bilan almashtiriladi (uning o'z testlari bor). Docker talab qiladi."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from medbron_common.http_client import ServiceClient, UpstreamUnavailable
from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token, sample_data

from app.clients.file import AttachedFile, FileClient, FileNotAttachable

pytestmark = pytest.mark.integration

BOOKING = {"X-Caller": "booking-service", "X-Internal-Token": "test-token"}
T0 = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


class FakeFiles:
    def __init__(self) -> None:
        self.attached: list[uuid.UUID] = []
        self.released: list[uuid.UUID] = []
        self.reject: set[uuid.UUID] = set()
        self.down = False

    async def attach_avatar(self, file_id: uuid.UUID, user_id: uuid.UUID) -> AttachedFile:
        if self.down:
            raise UpstreamUnavailable(extra={"target": "file-service"})
        if file_id in self.reject:
            raise FileNotAttachable("FILE_NOT_FOUND")
        self.attached.append(file_id)
        return AttachedFile(
            id=file_id, url=f"http://s3/public-media/{file_id}.jpg", thumbnail_url=f"http://s3/{file_id}_t"
        )

    async def release_avatar(self, file_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.released.append(file_id)

    async def aclose(self) -> None:
        return None


@pytest.fixture
def files(api: TestClient) -> FakeFiles:
    fake = FakeFiles()
    api.app.state.file_client = fake
    return fake


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def outbox(db, event_type: str) -> list[dict[str, Any]]:
    rows = db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
    for (payload,) in rows:
        assert_event_valid(payload)
    return [row[0]["data"] for row in rows]


def booking_event(event_type: str, uid: uuid.UUID, patient: str, booking_id: str, **extra: Any) -> dict:
    return sample_data(event_type, booking_id=booking_id, client_id=str(uid), patient_id=patient, **extra)


def test_profile_created_lazily_and_updated(api: TestClient, db, files, user_keys) -> None:
    uid = uuid.uuid4()
    h = bearer(mint_user_token(user_keys, user_id=uid, phone="+998901112233"))
    me = api.get("/v1/users/me", headers=h).json()
    assert me["id"] == str(uid) and me["phone"] == "+998901112233" and me["preferred_language"] == "uz"

    r = api.patch("/v1/users/me", json={"full_name": "Ali Valiyev", "preferred_language": "ru"}, headers=h)
    assert r.status_code == 200 and r.json()["full_name"] == "Ali Valiyev"
    assert outbox(db, "user.profile_updated") == [
        {"user_id": str(uid), "full_name": "Ali Valiyev", "preferred_language": "ru"}
    ]
    # O'zgarishsiz PATCH — event yo'q
    api.patch("/v1/users/me", json={"full_name": "Ali Valiyev"}, headers=h)
    assert len(outbox(db, "user.profile_updated")) == 1
    assert api.patch("/v1/users/me", json={"phone": "+1"}, headers=h).status_code == 422


def test_avatar_attach_replace_and_failures(api: TestClient, db, files, user_keys) -> None:
    h = bearer(mint_user_token(user_keys))
    first, second, bad = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    r = api.patch("/v1/users/me", json={"avatar_file_id": str(first)}, headers=h)
    assert r.json()["avatar_url"].endswith(f"{first}.jpg")
    api.patch("/v1/users/me", json={"avatar_file_id": str(second)}, headers=h)
    assert files.released == [first]  # eski avatar bo'shatildi
    assert outbox(db, "user.profile_updated") == []  # avatar — ism/til emas

    files.reject.add(bad)
    r = api.patch("/v1/users/me", json={"avatar_file_id": str(bad)}, headers=h)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "INVALID_AVATAR"
    files.down = True
    r = api.patch("/v1/users/me", json={"avatar_file_id": str(uuid.uuid4())}, headers=h)
    assert r.status_code == 503
    files.down = False
    r = api.patch("/v1/users/me", json={"avatar_file_id": None}, headers=h)
    assert r.json()["avatar_url"] is None and files.released == [first, second]

    # Profil o'chirilgan — biriktirilgan yangi fayl darhol bo'shatiladi (yetim qolmaydi)
    db.scalar("UPDATE profiles SET deleted_at = now()")
    late = uuid.uuid4()
    assert api.patch("/v1/users/me", json={"avatar_file_id": str(late)}, headers=h).status_code == 404
    assert files.released[-1] == late


def test_patients_crud_and_idor(api: TestClient, db, files, user_keys, run_handler) -> None:
    uid = uuid.uuid4()
    h = bearer(mint_user_token(user_keys, user_id=uid))
    other = bearer(mint_user_token(user_keys))
    body = {"full_name": "Onam", "relation": "Onam", "birth_date": "1965-03-01", "gender": "female"}
    p = api.post("/v1/users/me/patients", json=body, headers=h).json()
    assert api.get(f"/v1/users/me/patients/{p['id']}", headers=other).status_code == 404
    assert api.patch(f"/v1/users/me/patients/{p['id']}", json={"weight_kg": 70}, headers=other).status_code == 404
    r = api.patch(f"/v1/users/me/patients/{p['id']}", json={"weight_kg": 70}, headers=h)
    assert r.json()["weight_kg"] == 70
    assert [x["id"] for x in api.get("/v1/users/me/patients", headers=h).json()] == [p["id"]]

    # Internal: booking egalikni shu yerda tekshiradi
    assert api.get(f"/internal/v1/users/{uid}/patients/{p['id']}", headers=BOOKING).json()["full_name"] == "Onam"
    assert api.get(f"/internal/v1/users/{uuid.uuid4()}/patients/{p['id']}", headers=BOOKING).status_code == 404

    # Faol bron → o'chirib bo'lmaydi; yakunlangandan keyin — yumshoq o'chirish (tarix bor)
    bid = str(uuid.uuid4())
    run_handler("booking.created", booking_event("booking.created", uid, p["id"], bid), occurred_at=T0)
    r = api.delete(f"/v1/users/me/patients/{p['id']}", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ACTIVE_BOOKINGS_EXIST"
    run_handler(
        "booking.expired", booking_event("booking.expired", uid, p["id"], bid), occurred_at=T0 + timedelta(minutes=10)
    )
    assert api.delete(f"/v1/users/me/patients/{p['id']}", headers=h).status_code == 204
    assert db.scalar("SELECT is_deleted FROM patients WHERE id = :i", i=p["id"]) is True
    assert api.get(f"/internal/v1/users/{uid}/patients/{p['id']}", headers=BOOKING).status_code == 404

    # Bron qilinmagan bemor — jismonan o'chiriladi
    q = api.post("/v1/users/me/patients", json=body, headers=h).json()
    assert api.delete(f"/v1/users/me/patients/{q['id']}", headers=h).status_code == 204
    assert db.scalar("SELECT count(*) FROM patients WHERE id = :i", i=q["id"]) == 0


def test_addresses_single_default(api: TestClient, files, user_keys) -> None:
    uid = uuid.uuid4()
    h = bearer(mint_user_token(user_keys, user_id=uid))
    base = {"city": "Toshkent", "street": "Amir Temur 1", "latitude": "41.311081", "longitude": "69.240562"}
    a = api.post("/v1/users/me/addresses", json={**base, "is_default": True}, headers=h).json()
    b = api.post("/v1/users/me/addresses", json={**base, "label": "Ish", "is_default": True}, headers=h).json()
    listed = api.get("/v1/users/me/addresses", headers=h).json()
    assert [x["id"] for x in listed] == [b["id"], a["id"]] and [x["is_default"] for x in listed] == [True, False]
    api.patch(f"/v1/users/me/addresses/{a['id']}", json={"is_default": True}, headers=h)
    assert [x["is_default"] for x in api.get("/v1/users/me/addresses", headers=h).json() if x["id"] == b["id"]] == [
        False
    ]
    r = api.get(f"/internal/v1/users/{uid}/addresses/{a['id']}", headers=BOOKING)
    assert r.status_code == 200 and r.json()["latitude"] == "41.311081"
    assert api.post("/v1/users/me/addresses", json={**base, "latitude": "91"}, headers=h).status_code == 422
    assert api.delete(f"/v1/users/me/addresses/{a['id']}", headers=h).status_code == 204
    assert api.get(f"/v1/users/me/addresses/{a['id']}", headers=h).status_code == 404


def test_favorites_idempotent(api: TestClient, files, user_keys) -> None:
    h = bearer(mint_user_token(user_keys))
    doctor = str(uuid.uuid4())
    for _ in range(2):
        assert api.put(f"/v1/users/me/favorites/{doctor}", headers=h).status_code == 204
    assert [f["doctor_id"] for f in api.get("/v1/users/me/favorites", headers=h).json()] == [doctor]
    assert api.delete(f"/v1/users/me/favorites/{doctor}", headers=h).status_code == 204
    assert api.get("/v1/users/me/favorites", headers=h).json() == []


def test_account_deletion(api: TestClient, db, files, user_keys, admin_keys, run_handler) -> None:
    uid = uuid.uuid4()
    h = bearer(mint_user_token(user_keys, user_id=uid))
    avatar = uuid.uuid4()
    api.patch("/v1/users/me", json={"full_name": "Ali", "avatar_file_id": str(avatar)}, headers=h)
    p = api.post(
        "/v1/users/me/patients",
        json={"full_name": "Men", "birth_date": "1990-01-01", "gender": "male"},
        headers=h,
    ).json()
    bid = str(uuid.uuid4())
    run_handler(
        "booking.created", booking_event("booking.created", uid, p["id"], bid, status="confirmed"), occurred_at=T0
    )
    r = api.delete("/v1/users/me", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ACTIVE_BOOKINGS_EXIST"

    run_handler(
        "booking.completed", booking_event("booking.completed", uid, p["id"], bid), occurred_at=T0 + timedelta(days=4)
    )
    # Kechikib kelgan eski "confirmed" yakuniy holatni qaytarmaydi
    run_handler(
        "booking.created", booking_event("booking.created", uid, p["id"], bid, status="confirmed"), occurred_at=T0
    )
    assert db.scalar("SELECT status FROM booking_states WHERE booking_id = :b", b=bid) == "completed"

    doctor_token = bearer(mint_user_token(user_keys, user_id=uuid.uuid4(), roles=["client", "doctor"]))
    assert api.delete("/v1/users/me", headers=doctor_token).json()["detail"]["code"] == "DOCTOR_ACCOUNT"

    assert api.delete("/v1/users/me", headers=h).status_code == 204
    assert api.delete("/v1/users/me", headers=h).status_code == 204  # takror — jim
    assert files.released == [avatar]
    assert db.scalar("SELECT full_name FROM patients WHERE id = :i", i=p["id"]) == "O'chirilgan"
    assert outbox(db, "user.deletion_requested")[0]["user_id"] == str(uid)
    assert outbox(db, "audit.recorded")[0]["action"] == "user.delete"
    r = api.get("/v1/users/me", headers=h)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "ACCOUNT_DELETED"

    admin = bearer(mint_admin_token(admin_keys, permissions=["users.manage"]))
    info = api.get(f"/v1/admin/users/{uid}/profile", headers=admin).json()
    assert info["deleted_at"] and info["patients"] == 0 and info["active_bookings"] == 0
    assert api.get(f"/v1/admin/users/{uuid.uuid4()}/profile", headers=admin).status_code == 404


def test_user_registered_creates_profile(db, run_handler) -> None:
    uid = str(uuid.uuid4())
    data = {"user_id": uid, "phone": "+998901234567", "registered_at": "2026-10-21T08:00:00Z"}
    run_handler("user.registered", data)
    run_handler("user.registered", data)
    assert db.scalar("SELECT count(*) FROM profiles WHERE user_id = :u", u=uid) == 1


def test_every_catalog_consume_has_handler() -> None:
    from medbron_common.eventbus import EventConsumer
    from medbron_common.events import CATALOG

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="user-service", amqp_url="amqp://x/", session_factory=None, processed_model=ProcessedEvent
    )
    handlers.register(consumer)
    assert consumer.routing_keys == sorted(e for e, s in CATALOG.items() if "user-service" in s.consumers)


async def test_file_client_contract() -> None:
    """file-service internal API bilan HTTP kontrakt: yo'l, sarlavhalar, idempotency, xato tarjimasi."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/release"):
            return httpx.Response(204)
        if "bad" in request.headers.get("idempotency-key", "") or request.url.path.startswith("/internal/v1/files/0"):
            return httpx.Response(404, json={"detail": {"code": "FILE_NOT_FOUND", "message": "x"}})
        return httpx.Response(200, json={"id": request.url.path.split("/")[4], "url": "u", "thumbnail_url": "t"})

    client = FileClient("http://file-service:8080")
    client.http = ServiceClient(
        "file-service", "http://file-service:8080", caller="user-service", token="tok",
        transport=httpx.MockTransport(handler), backoff_base=0,
    )  # fmt: skip
    uid, fid = uuid.uuid4(), uuid.uuid4()
    attached = await client.attach_avatar(fid, uid)
    assert attached == AttachedFile(id=fid, url="u", thumbnail_url="t")
    req = seen[-1]
    assert req.url.path == f"/internal/v1/files/{fid}/attach" and req.headers["x-caller"] == "user-service"
    assert req.headers["x-internal-token"] == "tok" and req.headers["idempotency-key"].startswith("attach:")
    with pytest.raises(FileNotAttachable):
        await client.attach_avatar(uuid.UUID(int=0), uid)
    await client.release_avatar(fid, uid)
    assert seen[-1].url.path.endswith("/release")
    await client.aclose()
