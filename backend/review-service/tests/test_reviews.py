"""Sharh oqimi — haqiqiy Postgres + Redis; booking — tests/fakes.py; eventlar outbox'dan sxemaga tekshiriladi."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token, sample_data

from app.services import moderation, ratings
from tests.fakes import FakeDeps

CLINIC = uuid.UUID("22222222-2222-7222-8222-222222222222")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def fakes(api: TestClient, run_handler) -> FakeDeps:
    deps: FakeDeps = run_handler.deps
    api.app.state.deps = deps
    return deps


class World:
    def __init__(self, api: TestClient, fakes: FakeDeps, db, run_handler, user_keys, admin_keys) -> None:
        self.api, self.fakes, self.db, self.run = api, fakes, db, run_handler
        self.keys = user_keys
        self.doctor_id = uuid.uuid4()
        self.doctor = bearer(
            mint_user_token(user_keys, user_id=uuid.uuid4(), roles=["doctor"], doctor_id=self.doctor_id)
        )
        self.admin = bearer(mint_admin_token(admin_keys, permissions=["reviews.moderate"]))

    def client(self) -> tuple[uuid.UUID, dict[str, str]]:
        cid = uuid.uuid4()
        return cid, bearer(mint_user_token(self.keys, user_id=cid))

    def completed(self, client_id: uuid.UUID, *, days_ago: int = 1, clinic: uuid.UUID | None = CLINIC) -> uuid.UUID:
        booking_id = uuid.uuid4()
        at = datetime.now(UTC) - timedelta(days=days_ago)
        self.run("booking.completed", sample_data(
            "booking.completed", booking_id=str(booking_id), client_id=str(client_id), doctor_id=str(self.doctor_id),
            clinic_id=str(clinic) if clinic else None, completed_at=at.isoformat(), number="MB-261021-ABCD",
        ))  # fmt: skip
        return booking_id

    def review(self, h: dict[str, str], booking_id: uuid.UUID, rating: int = 5, **kw: Any) -> Any:
        return self.api.post("/v1/reviews", json={"booking_id": str(booking_id), "rating": rating, **kw}, headers=h)

    def events(self, event_type: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
        for (payload,) in rows:
            assert_event_valid(payload)
        return [r[0]["data"] for r in rows]


@pytest.fixture
def world(api: TestClient, fakes: FakeDeps, db, run_handler, user_keys, admin_keys) -> World:
    return World(api, fakes, db, run_handler, user_keys, admin_keys)


def test_clean_review_is_published_once_and_rating_hidden_below_five(world: World) -> None:
    cid, h = world.client()
    booking = world.completed(cid)
    r = world.review(h, booking, 4, comment="  Juda yaxshi shifokor  ", author_name="Aziz Toshmatov")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "published" and body["comment"] == "Juda yaxshi shifokor" and body["author"] == "Aziz T."
    again = world.review(h, booking, 5)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_REVIEWED"

    _stranger, sh = world.client()
    assert world.review(sh, booking).status_code == 404  # begona bron — topilmadi
    assert [e["status"] for e in world.events("review.submitted")] == ["published"]
    assert len(world.events("review.published")) == 1
    rating = world.events("rating.recalculated")[-1]
    assert rating == {
        "doctor_id": str(world.doctor_id), "rating": None, "reviews_count": 1, "clinic_id": str(CLINIC),
        "clinic_rating": None, "clinic_reviews_count": 1,
    }  # fmt: skip

    public = world.api.get(f"/v1/reviews?doctor_id={world.doctor_id}").json()
    assert [p["rating"] for p in public] == [4] and "client_id" not in public[0]
    assert world.api.get(f"/v1/reviews?clinic_id={CLINIC}").json()[0]["id"] == body["id"]
    assert world.api.get("/v1/reviews").status_code == 400
    mine = world.api.get("/v1/reviews/mine", headers=h).json()
    assert [m["booking_id"] for m in mine] == [str(booking)]


def test_rating_appears_from_five_reviews_and_anonymous_hides_author(world: World) -> None:
    for score in (5, 4, 5, 3, 4):
        cid, h = world.client()
        assert (
            world.review(h, world.completed(cid), score, is_anonymous=True, author_name="Ali Vali").status_code == 201
        )
    last = world.events("rating.recalculated")[-1]
    assert last["rating"] == 4.2 and last["reviews_count"] == 5 and last["clinic_rating"] == 4.2
    public = world.api.get(f"/v1/reviews?doctor_id={world.doctor_id}&limit=2").json()
    assert len(public) == 2 and all(p["author"] is None for p in public)


def test_window_and_not_completed(world: World) -> None:
    cid, h = world.client()
    old = world.completed(cid, days_ago=31)
    r = world.review(h, old)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "REVIEW_WINDOW_CLOSED"
    # proyeksiyada yo'q va booking ham yakunlanmagan deydi
    r = world.review(h, uuid.uuid4())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_REVIEWABLE"
    assert world.review(h, world.completed(cid), 6).status_code == 422


def test_projection_lag_falls_back_to_booking_then_fills_clinic(world: World) -> None:
    cid, h = world.client()
    booking = world.fakes.booking.add(cid, world.doctor_id)
    r = world.review(h, booking, 5)
    assert r.status_code == 201 and world.fakes.booking.calls == 1
    assert world.events("rating.recalculated")[-1]["clinic_id"] is None
    _other, oh = world.client()
    assert world.review(oh, booking).status_code == 404  # booking: not_owner
    # booking.completed keyin keldi — klinika to'ldiriladi va klinika reytingi hisoblanadi
    completed = sample_data(
        "booking.completed", booking_id=str(booking), client_id=str(cid), doctor_id=str(world.doctor_id),
        clinic_id=str(CLINIC), completed_at=datetime.now(UTC).isoformat(),
    )  # fmt: skip
    world.run("booking.completed", completed)
    world.run("booking.completed", completed)  # idempotent
    assert world.events("rating.recalculated")[-1]["clinic_reviews_count"] == 1
    assert world.db.scalar("SELECT count(*) FROM reviewable_bookings") == 1


def test_flagged_review_goes_to_moderation_and_admin_decides(world: World) -> None:
    cid, h = world.client()
    r = world.review(h, world.completed(cid), 1, comment="Ahmoq! Menga qo'ng'iroq qiling 90 123 45 67, t.me/spam")
    body = r.json()
    assert body["status"] == "pending"
    assert world.events("review.submitted")[0]["flags"] == ["profanity", "phone", "link"]
    assert world.events("rating.recalculated") == []  # pending reytingga ta'sir qilmaydi
    assert world.api.get(f"/v1/reviews?doctor_id={world.doctor_id}").json() == []

    queue = world.api.get("/v1/admin/reviews", headers=world.admin).json()
    assert [q["id"] for q in queue] == [body["id"]] and queue[0]["moderation_flags"]
    rej = world.api.post(f"/v1/admin/reviews/{body['id']}/reject", json={"reason": "Haqorat"}, headers=world.admin)
    assert rej.status_code == 200 and rej.json()["status"] == "rejected"
    assert world.events("review.rejected")[0]["reason"] == "Haqorat"
    assert world.events("rating.recalculated") == []
    assert world.api.post(f"/v1/admin/reviews/{body['id']}/publish", headers=world.admin).status_code == 409
    mine = world.api.get("/v1/reviews/mine", headers=h).json()[0]
    assert mine["status"] == "rejected" and mine["reject_reason"] == "Haqorat"

    cid2, h2 = world.client()
    flagged = world.review(h2, world.completed(cid2), 5, comment="www.example.uz ga kiring").json()
    ok = world.api.post(f"/v1/admin/reviews/{flagged['id']}/publish", headers=world.admin)
    assert ok.status_code == 200 and ok.json()["status"] == "published"
    assert world.events("rating.recalculated")[-1]["reviews_count"] == 1
    # e'lon qilinganini shikoyat bo'yicha olib tashlash — reyting qayta hisoblanadi
    take = world.api.post(f"/v1/admin/reviews/{flagged['id']}/reject", json={"reason": "Reklama"}, headers=world.admin)
    assert take.status_code == 200 and world.events("rating.recalculated")[-1]["reviews_count"] == 0
    audit = [e["action"] for e in world.events("audit.recorded")]
    assert audit == ["review.reject", "review.publish", "review.reject"]
    assert world.api.post(f"/v1/admin/reviews/{uuid.uuid4()}/publish", headers=world.admin).status_code == 404
    all_rows = world.api.get("/v1/admin/reviews?status=", headers=world.admin).json()
    assert len(all_rows) == 2


def test_doctor_replies_once_and_only_to_own(world: World, user_keys) -> None:
    cid, h = world.client()
    review = world.review(h, world.completed(cid), 5).json()
    url = f"/v1/doctor/reviews/{review['id']}/reply"
    bad = world.api.post(url, json={"text": "Qo'ng'iroq qiling +998 90 123 45 67"}, headers=world.doctor)
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "INVALID_REPLY"
    assert world.api.post(url, json={"text": "   "}, headers=world.doctor).status_code == 400
    ok = world.api.post(url, json={"text": "Rahmat!"}, headers=world.doctor)
    assert ok.status_code == 200 and ok.json()["doctor_reply"] == "Rahmat!"
    again = world.api.post(url, json={"text": "Yana"}, headers=world.doctor)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "ALREADY_REPLIED"
    other = bearer(mint_user_token(user_keys, user_id=uuid.uuid4(), roles=["doctor"], doctor_id=uuid.uuid4()))
    assert world.api.post(url, json={"text": "x"}, headers=other).status_code == 404
    assert world.api.post(url, json={"text": "x"}, headers=h).status_code == 403  # shifokor emas
    mine = world.api.get("/v1/doctor/reviews", headers=world.doctor).json()
    assert mine[0]["doctor_reply"] == "Rahmat!"


def test_settings_changed_invalidates_cache(world: World) -> None:
    world.run("admin.settings_changed", sample_data("admin.settings_changed"))
    assert world.fakes.settings.invalidated == 1


def test_moderation_rules_and_rating_rounding() -> None:
    assert moderation.flags("Zo'r shifokor, rahmat") == []
    assert moderation.flags("ДУРАК") == ["profanity"]
    assert moderation.flags("@my_channel ga obuna") == ["link"]
    assert moderation.flags("+998 (90) 123-45-67") == ["phone"]
    # Monolit regex'i buni o'tkazib yuborardi (raqamlar orasida `\b` yo'q)
    assert moderation.flags("qo'ng'iroq qiling +998901234567") == ["phone"]
    assert moderation.flags("998901234567") == ["phone"]
    assert moderation.flags("narxi 150000 so'm, 2026-10-21 kuni") == []
    assert moderation.flags("buyurtma 12345678901234") == []
    assert moderation.short_name("Aziz") == "Aziz" and moderation.short_name("  ") == ""
    assert ratings.shown(4.125, 5) == 4.13 and ratings.shown(5.0, 4) is None and ratings.shown(None, 0) is None
