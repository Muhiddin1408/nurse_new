"""5-bosqich e2e: real-time va qo'shimchalar (ws-gateway, review, dispute, analytics, search) Kong orqali.

- WebSocket: mijoz ulanadi, bronga obuna bo'ladi, to'laydi → `payment.captured` va `booking.confirmed` keladi;
  begona bronga obuna — FORBIDDEN.
- Sharh: yakunlangan qabul (vaqtni "sakratish" — `booking.completed` booking konteyneri ichidan, money e2e'dagidek)
  → sharh → ommaviy ro'yxatda → shifokor javobi; catalog'da reyting hisoblagichi.
- Nizo: to'langan bron → nizo → admin hal qiladi (shifokor javobgar emas) → payment refund (Payme → manual) →
  admin tasdiqlaydi → `refund.succeeded` → nizo yopiladi.
- Analitika: bugungi bronlar admin hisobotida.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import payments
import pytest
from conftest import BASE, bearer, eventually
from test_booking_flow import TASHKENT, client, ok, publish_event, world
from test_money_flow import _home_booking
from websockets.sync.client import connect

__all__ = ["client", "world"]

WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")


class Socket:
    def __init__(self, token: str) -> None:
        self.conn = connect(f"{WS_BASE}/ws?token={token}", open_timeout=10)

    def send(self, payload: dict[str, Any]) -> None:
        self.conn.send(json.dumps(payload))

    def until(self, predicate, timeout: float = 30.0) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        deadline = datetime.now(UTC).timestamp() + timeout
        while True:
            left = deadline - datetime.now(UTC).timestamp()
            if left <= 0:
                raise AssertionError("WebSocket xabari kelmadi")
            msg = json.loads(self.conn.recv(timeout=left))
            if predicate(msg):
                return dict(msg)

    def close(self) -> None:
        self.conn.close()


def _paid_home_booking(http: httpx.Client, world: dict[str, Any], client: dict[str, Any], offset: int) -> dict[str, Any]:
    booking = _home_booking(http, world, client, offset)
    payments.checkout(http, client["h"], booking["id"])
    payments.payme_pay(http, booking["id"], booking["prepay_tiyin"])

    def confirmed() -> dict[str, Any]:
        b = ok(http.get(f"/v1/bookings/{booking['id']}", headers=client["h"]))
        assert b["status"] == "confirmed", b
        return dict(b)

    return dict(eventually(confirmed))


def _complete_now(http: httpx.Client, world: dict[str, Any], client: dict[str, Any], b: dict[str, Any], ah) -> None:  # type: ignore[no-untyped-def]
    payment = ok(http.get("/v1/admin/payments", headers=ah, params={"booking_id": b["id"]}))[0]
    publish_event("booking.completed", "booking", b["id"], {
        "booking_id": b["id"], "number": b["number"], "client_id": client["id"], "patient_id": client["patient_id"],
        "doctor_id": world["doctor_id"], "clinic_id": None, "slot_id": str(uuid.uuid4()), "place": "home",
        "start_at": b["start_at"], "end_at": b["end_at"], "completed_at": datetime.now(UTC).isoformat(),
        "completed_by": "doctor", "payment_mode": b["payment_mode"], "paid_tiyin": b["paid_tiyin"],
        "money": payment["money"], "items": b["items"],
    }, producer="booking-service")  # fmt: skip


def test_websocket_delivers_payment_and_confirmation(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any], login
) -> None:
    token = client["h"]["Authorization"].removeprefix("Bearer ")
    sock = Socket(token)
    try:
        booking = _home_booking(http, world, client, 11)
        sock.send({"action": "subscribe", "channel": f"booking:{booking['id']}"})
        sock.until(lambda m: m.get("type") == "subscribed")
        sock.send({"action": "subscribe", "channel": f"booking:{uuid.uuid4()}"})
        denied = sock.until(lambda m: m.get("type") == "error")
        assert denied["code"] == "FORBIDDEN"

        payments.checkout(http, client["h"], booking["id"])
        payments.payme_pay(http, booking["id"], booking["prepay_tiyin"])
        captured = sock.until(lambda m: m.get("type") == "payment.captured")
        assert captured["data"]["booking_id"] == booking["id"]
        confirmed = sock.until(lambda m: m.get("type") == "booking.confirmed")
        assert confirmed["data"]["booking_id"] == booking["id"] and "phone" not in json.dumps(confirmed)
    finally:
        sock.close()
    # Tokensiz — ulanib bo'lmaydi
    with pytest.raises(Exception):  # noqa: B017, PT011 — handshake rad etiladi (InvalidStatus)
        connect(f"{WS_BASE}/ws", open_timeout=10)


def test_review_after_completed_visit_and_doctor_reply(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any], admin_token: str
) -> None:
    ah = bearer(admin_token)
    b = _paid_home_booking(http, world, client, 12)
    _complete_now(http, world, client, b, ah)

    def submitted() -> dict[str, Any]:
        r = http.post("/v1/reviews", headers=client["h"], json={
            "booking_id": b["id"], "rating": 5, "comment": "Juda yaxshi shifokor", "author_name": "Aziz Tursunov",
        })  # fmt: skip
        assert r.status_code == 201, r.text
        return dict(r.json())

    review = eventually(submitted, timeout=60)
    assert review["status"] == "published"
    again = http.post("/v1/reviews", headers=client["h"], json={"booking_id": b["id"], "rating": 4})
    assert again.status_code == 409  # bitta bronga bitta sharh

    def public() -> None:
        rows = ok(http.get("/v1/reviews", params={"doctor_id": world["doctor_id"]}))
        assert review["id"] in {r["id"] for r in rows}, rows

    eventually(public)
    reply = http.post(f"/v1/doctor/reviews/{review['id']}/reply", headers=world["doctor"], json={"text": "Rahmat!"})
    assert reply.status_code == 200, reply.text
    stranger = http.post(f"/v1/doctor/reviews/{review['id']}/reply", headers=client["h"], json={"text": "x"})
    assert stranger.status_code in (403, 404)

    # Bayroqli sharh (telefon raqami) — moderatsiyaga, admin ko'radi
    b2 = _paid_home_booking(http, world, client, 13)
    _complete_now(http, world, client, b2, ah)

    def flagged() -> dict[str, Any]:
        r = http.post("/v1/reviews", headers=client["h"], json={
            "booking_id": b2["id"], "rating": 1, "comment": "Menga qo'ng'iroq qiling +998901234567",
        })  # fmt: skip
        assert r.status_code == 201, r.text
        return dict(r.json())

    pending = eventually(flagged, timeout=60)
    assert pending["status"] == "pending"
    queue = ok(http.get("/v1/admin/reviews", headers=ah, params={"status": "pending"}))
    assert pending["id"] in {r["id"] for r in queue}
    ok(http.post(f"/v1/admin/reviews/{pending['id']}/reject", headers=ah, json={"reason": "Shaxsiy ma'lumot"}))


def test_dispute_refund_closes_dispute(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any], admin_token: str
) -> None:
    ah = bearer(admin_token)
    b = _paid_home_booking(http, world, client, 14)
    dispute = ok(http.post("/v1/disputes", headers=client["h"], json={
        "booking_id": b["id"], "reason": "poor_service", "description": "E2E nizo",
    }), 201)  # fmt: skip
    dup = http.post("/v1/disputes", headers=client["h"], json={"booking_id": b["id"], "reason": "other"})
    assert dup.status_code == 409
    assert dispute["id"] in {d["id"] for d in ok(http.get("/v1/disputes/mine", headers=client["h"]))}

    partial = b["paid_tiyin"] // 2
    resolved = ok(http.post(f"/v1/admin/disputes/{dispute['id']}/resolve", headers=ah, json={
        "resolution": "refund_partial", "refund_tiyin": partial, "doctor_liable": False, "note": "E2E",
    }))  # fmt: skip
    assert resolved["status"] == "resolved"

    def queued() -> dict[str, Any]:
        rows = ok(http.get("/v1/admin/refunds", headers=ah, params={"booking_id": b["id"]}))
        mine = [r for r in rows if r["source"] == "dispute"]
        assert mine and mine[0]["status"] == "manual_required", rows
        return dict(mine[0])

    refund = eventually(queued, timeout=120)
    assert refund["amount_tiyin"] == partial and refund["doctor_share_tiyin"] == 0
    ok(http.post(f"/v1/admin/refunds/{refund['id']}/complete", headers=ah, json={"external_ref": "CABINET-DISPUTE"}))

    def closed() -> None:
        d = ok(http.get(f"/v1/disputes/{dispute['id']}", headers=client["h"]))
        assert d["status"] == "closed" and d["refunded_tiyin"] == partial, d

    eventually(closed, timeout=60)


def test_analytics_sees_todays_bookings(http: httpx.Client, admin_token: str) -> None:
    ah = bearer(admin_token)
    today = datetime.now(TASHKENT).date().isoformat()

    def counted() -> None:
        summary = ok(http.get("/v1/admin/analytics/summary", headers=ah, params={"from": today, "to": today}))
        # Shu modul testlari bugun bron yaratdi, to'ladi va refund oldi
        assert summary["created"] >= 1 and summary["captured_tiyin"] > 0, summary

    eventually(counted, timeout=60)
    assert http.get("/v1/admin/analytics/summary", params={"from": today, "to": today}).status_code == 401


def test_search_finds_doctor_reindex_and_hides_suspended(
    http: httpx.Client, world: dict[str, Any], admin_token: str
) -> None:
    """Modulning OXIRGI testi: shifokorni vaqtincha to'xtatadi (kelajakdagi bronlari bekor bo'ladi)."""
    ah = bearer(admin_token)
    name = world["spec"]["name"].split()[-1]  # E2E Shifokor <tag> — tag mutaxassislik nomida ham bor

    def ids() -> set[str]:
        found = ok(http.get("/v1/search/doctors", params={"q": f"E2E Shifokor {name}"}))
        return {d["id"] for d in found["items"]}

    def visible() -> None:
        assert world["doctor_id"] in ids()

    eventually(visible, timeout=60)

    run = ok(http.post("/v1/admin/search/reindex", headers=ah), 202)

    def reindexed() -> None:
        runs = ok(http.get("/v1/admin/search/reindex", headers=ah))
        mine = next(r for r in runs if r["id"] == run["id"])
        assert mine["status"] == "done", mine

    eventually(reindexed, timeout=180)
    assert world["doctor_id"] in ids()  # alias almashgandan keyin ham topiladi

    ok(http.post(f"/v1/admin/moderation/doctors/{world['doctor_id']}/suspend", headers=ah, json={"reason": "e2e"}))
    try:
        eventually(lambda: _absent(world["doctor_id"], ids()), timeout=60)
    finally:
        ok(http.post(f"/v1/admin/moderation/doctors/{world['doctor_id']}/reinstate", headers=ah, json={"reason": "e2e"}))
    eventually(visible, timeout=60)


def _absent(item: str, found: set[str]) -> None:
    assert item not in found, found
