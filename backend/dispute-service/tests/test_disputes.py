"""Nizo oqimi — haqiqiy Postgres + Redis, upstream'lar — tests/fakes.py. Eventlar outbox'dan o'qilib sxemaga
tekshiriladi."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token, sample_data

from tests.fakes import DOCTOR, FakeDeps


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fakes(api: TestClient, run_handler) -> FakeDeps:
    deps: FakeDeps = run_handler.deps
    api.app.state.deps = deps
    return deps


class World:
    def __init__(self, api: TestClient, fakes: FakeDeps, db, user_keys, admin_keys) -> None:
        self.api, self.fakes, self.db = api, fakes, db
        self.client_id = uuid.uuid4()
        self.user = bearer(mint_user_token(user_keys, user_id=self.client_id))
        self.admin_id = uuid.uuid4()
        self.admin = bearer(mint_admin_token(admin_keys, admin_id=self.admin_id, permissions=["disputes.resolve"]))
        self.user_keys = user_keys

    def booking(self, **kw: Any) -> uuid.UUID:
        return self.fakes.booking.add(self.client_id, **kw)

    def open(self, booking_id: uuid.UUID, reason: str = "doctor_no_show", **kw: Any) -> dict[str, Any]:
        r = self.api.post(
            "/v1/disputes", json={"booking_id": str(booking_id), "reason": reason, **kw}, headers=self.user
        )
        assert r.status_code == 201, r.text
        return dict(r.json())

    def resolve(self, dispute_id: str, **body: Any) -> Any:
        return self.api.post(f"/v1/admin/disputes/{dispute_id}/resolve", json={"note": "ko'rib chiqildi", **body},
                             headers=self.admin)  # fmt: skip

    def events(self, event_type: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
        for (payload,) in rows:
            assert_event_valid(payload)
        return [r[0]["data"] for r in rows]


@pytest.fixture
def world(api: TestClient, fakes: FakeDeps, db, user_keys, admin_keys) -> World:
    return World(api, fakes, db, user_keys, admin_keys)


def test_open_dispute_guards_and_sla(world: World) -> None:
    b = world.booking()
    d = world.open(b, description="Shifokor kelmadi, 2 soat kutdim")
    assert d["status"] == "open" and d["booking_number"].startswith("MB-") and d["evidence"] == []
    opened = world.events("dispute.opened")
    assert (
        len(opened) == 1 and opened[0]["raised_by"] == str(world.client_id) and opened[0]["reason"] == "doctor_no_show"
    )
    hours = world.db.scalar("SELECT extract(epoch FROM due_at - created_at) / 3600 FROM disputes")
    assert round(float(hours)) == 48  # admin `sla.dispute_hours`

    # Bitta bronga bitta nizo; begona / yo'q bron — 404; to'lanmagan / bekor qilingan bron — 409
    r = world.api.post("/v1/disputes", json={"booking_id": str(b), "reason": "other"}, headers=world.user)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DISPUTE_EXISTS"
    foreign = world.fakes.booking.add(uuid.uuid4())
    r = world.api.post("/v1/disputes", json={"booking_id": str(foreign), "reason": "other"}, headers=world.user)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "BOOKING_NOT_FOUND"
    cancelled = world.booking(status="cancelled")
    r = world.api.post("/v1/disputes", json={"booking_id": str(cancelled), "reason": "other"}, headers=world.user)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_DISPUTABLE"
    r = world.api.post("/v1/disputes", json={"booking_id": str(b), "reason": "angry"}, headers=world.user)
    assert r.status_code == 422

    mine = world.api.get("/v1/disputes/mine", headers=world.user).json()
    assert [x["id"] for x in mine] == [d["id"]]
    assert world.api.get(f"/v1/disputes/{d['id']}", headers=world.user).status_code == 200
    stranger = bearer(mint_user_token(world.user_keys, user_id=uuid.uuid4()))
    assert world.api.get(f"/v1/disputes/{d['id']}", headers=stranger).status_code == 404


def test_evidence_attach_idempotent_and_limits(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    d = world.open(world.booking())
    file_id = world.fakes.files.upload(world.client_id, "image/jpeg")
    path = f"/v1/disputes/{d['id']}/evidence"
    r = world.api.post(path, json={"file_id": str(file_id)}, headers=world.user)
    assert r.status_code == 200 and [e["file_id"] for e in r.json()["evidence"]] == [str(file_id)]
    assert r.json()["evidence"][0]["content_type"] == "image/jpeg" and r.json()["evidence"][0]["url"] is None
    again = world.api.post(path, json={"file_id": str(file_id)}, headers=world.user)
    assert len(again.json()["evidence"]) == 1  # idempotent
    assert world.fakes.files.files[file_id]["ref"] == f"dispute:{d['id']}"

    # begona fayl (boshqa user yuklagan) — biriktirilmaydi
    alien = world.fakes.files.upload(uuid.uuid4())
    r = world.api.post(path, json={"file_id": str(alien)}, headers=world.user)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "EVIDENCE_REJECTED"
    # begona nizo — 404
    stranger = bearer(mint_user_token(world.user_keys, user_id=uuid.uuid4()))
    assert world.api.post(path, json={"file_id": str(file_id)}, headers=stranger).status_code == 404

    from app.core.config import get_settings
    from app.services import disputes

    limited = get_settings().model_copy(update={"max_evidence_files": 1})
    monkeypatch.setattr(disputes, "get_settings", lambda: limited)
    more = world.fakes.files.upload(world.client_id)
    r = world.api.post(path, json={"file_id": str(more)}, headers=world.user)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TOO_MANY_EVIDENCE"

    # admin — dalil havolasi (file-service auditiga admin ID bilan)
    detail = world.api.get(f"/v1/admin/disputes/{d['id']}", headers=world.admin).json()
    assert detail["evidence"][0]["url"].startswith("https://s3.local/private/")
    assert world.fakes.files.urls == [(file_id, str(world.admin_id))]
    world.fakes.files.url_down = True  # file-service yiqilsa — sahifa ochiladi, havola bo'sh
    assert world.api.get(f"/v1/admin/disputes/{d['id']}", headers=world.admin).json()["evidence"][0]["url"] is None


def test_partial_refund_resolution_closes_on_refund_succeeded(world: World, run_handler) -> None:
    b = world.booking(paid=15_000_000)
    d = world.open(b, reason="poor_service")
    bad = [
        {"resolution": "refund_partial", "refund_tiyin": 15_000_000},
        {"resolution": "refund_partial", "refund_tiyin": 0},
        {"resolution": "refund_full", "refund_tiyin": 1},
        {"resolution": "rejected", "refund_tiyin": 100},
        {"resolution": "rejected", "doctor_liable": True},
    ]
    for body in bad:
        r = world.resolve(d["id"], **body)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "INVALID_RESOLUTION", body
    assert world.resolve(d["id"], resolution="refund_partial", refund_tiyin=5_000_000, extra=1).status_code == 422

    r = world.resolve(d["id"], resolution="refund_partial", refund_tiyin=5_000_000, doctor_liable=True)
    assert r.status_code == 200 and r.json()["status"] == "resolved" and r.json()["resolved_by"] == str(world.admin_id)
    again = world.resolve(d["id"], resolution="refund_partial", refund_tiyin=5_000_000, doctor_liable=True)
    assert again.status_code == 200  # idempotent (o'sha qaror)
    other = world.resolve(d["id"], resolution="rejected")
    assert other.status_code == 409 and other.json()["detail"]["code"] == "INVALID_STATE"
    resolved = world.events("dispute.resolved")
    assert len(resolved) == 1
    assert (resolved[0]["resolution"], resolved[0]["refund_tiyin"], resolved[0]["doctor_liable"]) == (
        "refund_partial",
        5_000_000,
        True,
    )
    audit = world.events("audit.recorded")
    assert [a["action"] for a in audit] == ["dispute.resolve"] and audit[0]["actor_id"] == str(world.admin_id)

    # dalil yopilgan nizoga emas — resolved holatda ham qo'shilmaydi
    f = world.fakes.files.upload(world.client_id)
    r = world.api.post(f"/v1/disputes/{d['id']}/evidence", json={"file_id": str(f)}, headers=world.user)
    assert r.status_code == 409

    # payment qaytardi (boshqa manba — e'tiborsiz; o'z manbasi — yopiladi; takror — o'zgarishsiz)
    run_handler("refund.succeeded", sample_data("refund.succeeded", booking_id=str(b), source="booking_cancelled",
                                                source_ref=str(b)))  # fmt: skip
    assert world.db.scalar("SELECT status FROM disputes") == "resolved"
    refund = sample_data(
        "refund.succeeded", booking_id=str(b), source="dispute", source_ref=d["id"], amount_tiyin=4_000_000,
        refund_id=str(uuid.uuid4()),
    )  # fmt: skip
    run_handler("refund.succeeded", refund)
    run_handler("refund.succeeded", {**refund, "amount_tiyin": 1})
    mine = world.api.get(f"/v1/disputes/{d['id']}", headers=world.user).json()
    assert mine["status"] == "closed" and mine["refunded_tiyin"] == 4_000_000 and mine["closed_at"]
    run_handler("refund.succeeded", {**refund, "source_ref": str(uuid.uuid4()), "refund_id": str(uuid.uuid4())})


def test_full_refund_and_rejection(world: World) -> None:
    full = world.open(world.booking(paid=3_000_000))  # zaklad — to'langan qism
    r = world.resolve(full["id"], resolution="refund_full")
    assert r.status_code == 200 and r.json()["refund_tiyin"] == 3_000_000 and r.json()["doctor_liable"] is False
    rejected = world.open(world.booking())
    r = world.resolve(rejected["id"], resolution="rejected")
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["refund_tiyin"] == 0
    at_clinic = world.open(world.booking(paid=0))
    r = world.resolve(at_clinic["id"], resolution="refund_full")
    assert r.status_code == 400  # klinikada to'langan — pul platformadan o'tmagan
    assert world.resolve(at_clinic["id"], resolution="rejected").status_code == 200
    assert world.resolve(str(uuid.uuid4()), resolution="rejected").status_code == 404
    events = world.events("dispute.resolved")
    assert [(e["resolution"], e["refund_tiyin"]) for e in events] == [
        ("refund_full", 3_000_000),
        ("rejected", 0),
        ("rejected", 0),
    ]
    queue = world.api.get("/v1/admin/disputes?status=", headers=world.admin).json()
    assert len(queue) == 3 and all(q["doctor_id"] == str(DOCTOR) for q in queue)
    assert world.api.get("/v1/admin/disputes", headers=world.admin).json() == []  # standart — faqat ochiqlar


def test_sla_breach_is_reported_once(world: World, run_async) -> None:
    from app.services import disputes

    late = world.open(world.booking())
    fresh = world.open(world.booking())
    world.db.scalar("UPDATE disputes SET due_at = now() - interval '1 hour' WHERE id = :id", id=uuid.UUID(late["id"]))
    assert run_async(disputes.check_sla) == 1
    assert run_async(disputes.check_sla) == 0  # bir marta
    breached = world.events("dispute.sla_breached")
    assert [e["dispute_id"] for e in breached] == [late["id"]]
    overdue = world.api.get("/v1/admin/disputes?overdue=true", headers=world.admin).json()
    assert [o["id"] for o in overdue] == [late["id"]] and overdue[0]["sla_breached_at"]
    assert fresh["id"] not in {o["id"] for o in overdue}
    by_booking = world.api.get(f"/v1/admin/disputes?booking_id={late['booking_id']}", headers=world.admin).json()
    assert len(by_booking) == 1


def test_settings_changed_invalidates_cache(world: World, run_handler) -> None:
    run_handler("admin.settings_changed", sample_data("admin.settings_changed"))
    assert world.fakes.settings.invalidated == 1
