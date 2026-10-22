"""4-bosqich e2e: pul (booking ↔ payment ↔ billing) Kong orqali, haqiqiy provayder protokoli bilan.

- Payme to'lovi → `payment.captured` → bron tasdiqlanadi; bekor qilish → refund (Payme API qila olmaydi →
  `manual_required`) → admin kabinetda qaytarganini tasdiqlaydi → daftar balansda.
- Saga kompensatsiyasi: Payme tranzaksiyasi ochiq turganda bron bekor qilindi, keyin pul yechildi →
  `booking.confirmation_failed` → to'liq refund avtomatik navbatga.
- Click to'lovi → (o'tgan haftada yakunlangan qabul) → haftalik payout → mark-paid → `payout.paid` → payment
  ledger'ida shifokorga qarz yopiladi. Qabul vaqtini "sakratish" uchun `booking.completed` booking konteyneri
  ichidan yuboriladi (qolgan hamma narsa — haqiqiy servislar).
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from typing import Any

import httpx
import payments
from conftest import bearer, eventually
from test_booking_flow import CLIENT_POINT, TASHKENT, book, client, day, free_slots, ok, publish_event, world

__all__ = ["client", "world"]  # modul fixture'lari (shifokor, klinika, xizmatlar) — test_booking_flow dan


def _home_booking(http: httpx.Client, world: dict[str, Any], client: dict[str, Any], offset: int) -> dict[str, Any]:
    address = ok(http.post("/v1/users/me/addresses", headers=client["h"], json={
        "city": "Toshkent", "street": "Bobur 5", "latitude": str(CLIENT_POINT[0]), "longitude": str(CLIENT_POINT[1]),
    }), 201)  # fmt: skip
    slot = eventually(lambda: free_slots(http, world["doctor_id"], day(offset), "home")[0])
    booking = ok(book(http, client, world, slot, "home_service", address_id=address["id"]), 201)
    assert booking["status"] == "pending_payment" and booking["prepay_tiyin"] > 0
    return dict(booking)


def _booking(http: httpx.Client, client: dict[str, Any], booking_id: str) -> dict[str, Any]:
    return dict(ok(http.get(f"/v1/bookings/{booking_id}", headers=client["h"])))


def _doctor_payable(http: httpx.Client, ah: dict[str, str], doctor_id: str) -> int:
    r = ok(http.get("/v1/admin/ledger/balances", headers=ah, params={"doctor_id": doctor_id}))
    return int(r["balances"]["doctor_payable"])


def _trial_balance(http: httpx.Client, ah: dict[str, str]) -> int:
    return int(ok(http.get("/v1/admin/ledger/balances", headers=ah))["trial_balance"])


def test_payme_payment_confirms_and_cancel_refund_is_completed_manually(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any], admin_token: str
) -> None:
    ah = bearer(admin_token)
    booking = _home_booking(http, world, client, 8)
    before = _doctor_payable(http, ah, world["doctor_id"])

    started = payments.checkout(http, client["h"], booking["id"])
    assert started["checkout_url"].startswith("https://checkout.paycom.uz/")
    assert payments.checkout(http, client["h"], booking["id"])["payment"]["id"] == started["payment"]["id"]
    payment_id = started["payment"]["id"]
    payments.payme_pay(http, booking["id"], booking["prepay_tiyin"])

    def confirmed() -> None:
        b = _booking(http, client, booking["id"])
        assert b["status"] == "confirmed" and b["paid_tiyin"] == booking["prepay_tiyin"], b

    eventually(confirmed)
    assert ok(http.get(f"/v1/payments/{payment_id}", headers=client["h"]))["status"] == "succeeded"
    # Begona mijoz to'lovni ko'rmaydi; ikkinchi marta to'lab bo'lmaydi
    again = http.post("/v1/payments", headers=client["h"], json={"booking_id": booking["id"], "provider": "click"})
    assert again.status_code == 409
    share = _doctor_payable(http, ah, world["doctor_id"]) - before
    assert 0 < share < booking["prepay_tiyin"]

    # > 24 soat oldin bekor — to'liq refund; Payme Merchant API refund qila olmaydi → admin navbati
    ok(http.post(f"/v1/bookings/{booking['id']}/cancel", headers=client["h"], json={"reason": "e2e"}))

    def queued() -> dict[str, Any]:
        rows = ok(http.get("/v1/admin/refunds", headers=ah, params={"booking_id": booking["id"]}))
        assert len(rows) == 1 and rows[0]["status"] == "manual_required", rows
        return dict(rows[0])

    refund = eventually(queued, timeout=120)  # scheduler har daqiqa
    assert refund["amount_tiyin"] == booking["prepay_tiyin"] and refund["source"] == "booking_cancelled"
    done = ok(http.post(f"/v1/admin/refunds/{refund['id']}/complete", headers=ah,
                        json={"external_ref": "CABINET-E2E", "note": "Payme kabinetida bekor qilindi"}))  # fmt: skip
    assert done["status"] == "succeeded"
    detail = ok(http.get(f"/v1/admin/payments/{payment_id}", headers=ah))
    assert detail["status"] == "refunded"
    assert _doctor_payable(http, ah, world["doctor_id"]) == before
    assert _trial_balance(http, ah) == 0


def test_money_taken_after_booking_cancelled_is_refunded_by_saga(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any], admin_token: str
) -> None:
    ah = bearer(admin_token)
    booking = _home_booking(http, world, client, 9)
    payment_id = payments.checkout(http, client["h"], booking["id"])["payment"]["id"]
    tx = payments.payme_open(http, booking["id"], booking["prepay_tiyin"])  # mijoz Payme sahifasida

    # shu orada bron bekor qilindi (pul hali yechilmagan) ...
    ok(http.post(f"/v1/bookings/{booking['id']}/cancel", headers=client["h"], json={"reason": "e2e saga"}))
    # ... lekin Payme baribir yechdi: provayderga MUVAFFAQIYAT (pul haqiqati), booking esa kompensatsiya qiladi
    performed = payments.payme(http, "PerformTransaction", {"id": tx})
    assert performed["result"]["state"] == 2, performed

    def compensated() -> None:
        detail = ok(http.get(f"/v1/admin/payments/{payment_id}", headers=ah))
        assert detail["needs_refund"] is True, detail
        assert [(r["source"], r["amount_tiyin"]) for r in detail["refunds"]] == [
            ("confirmation_failed", booking["prepay_tiyin"])
        ], detail["refunds"]

    eventually(compensated, timeout=60)
    assert _booking(http, client, booking["id"])["status"] == "cancelled"
    assert _trial_balance(http, ah) == 0


def test_click_payment_completed_visit_weekly_payout_closes_doctor_debt(
    http: httpx.Client, world: dict[str, Any], client: dict[str, Any], admin_token: str
) -> None:
    ah = bearer(admin_token)
    booking = _home_booking(http, world, client, 10)
    payment_id = payments.checkout(http, client["h"], booking["id"], "click")["payment"]["id"]
    payments.click_pay(http, booking["id"], booking["prepay_tiyin"])
    eventually(lambda: _assert_equal(_booking(http, client, booking["id"])["status"], "confirmed"))
    money = ok(http.get(f"/v1/admin/payments/{payment_id}", headers=ah))["money"]

    # O'tgan hafta yakshanbasi 12:00 (Toshkent) yakunlangan qabul
    today = datetime.now(TASHKENT).date()
    last_sunday = today - timedelta(days=today.weekday() + 1)
    completed_at = datetime.combine(last_sunday, time(12, 0), tzinfo=TASHKENT)
    b = _booking(http, client, booking["id"])
    publish_event("booking.completed", "booking", booking["id"], {
        "booking_id": b["id"], "number": b["number"], "client_id": client["id"], "patient_id": client["patient_id"],
        "doctor_id": world["doctor_id"], "clinic_id": None, "slot_id": str(uuid.uuid4()), "place": "home",
        "start_at": b["start_at"], "end_at": b["end_at"], "completed_at": completed_at.isoformat(),
        "completed_by": "doctor", "payment_mode": b["payment_mode"], "paid_tiyin": b["paid_tiyin"], "money": money,
        "items": b["items"],
    }, producer="booking-service")  # fmt: skip

    def built() -> dict[str, Any]:
        rows = ok(http.post("/v1/admin/payouts/build", headers=ah, json={"period_end": last_sunday.isoformat()}))
        mine = [p for p in rows if p["doctor_id"] == world["doctor_id"]]
        assert mine, rows
        return dict(mine[0])

    payout = eventually(built, timeout=60)
    assert payout["status"] == "pending" and payout["net_tiyin"] == money["doctor_payout_tiyin"]
    assert payout["period_end"] == last_sunday.isoformat() and payout["bookings_count"] == 1
    rebuilt = ok(http.post("/v1/admin/payouts/build", headers=ah, json={"period_end": last_sunday.isoformat()}))
    assert world["doctor_id"] not in {p["doctor_id"] for p in rebuilt}  # idempotent

    # Daromad paneli qabul sanasi (`start_at`) bo'yicha — monolitdagidek
    visit_day = datetime.fromisoformat(b["start_at"]).astimezone(TASHKENT).date().isoformat()
    earnings = ok(http.get("/v1/doctor/earnings", headers=world["doctor"], params={"from": visit_day, "to": visit_day}))
    assert earnings["net_tiyin"] >= payout["net_tiyin"]

    debt = _doctor_payable(http, ah, world["doctor_id"])
    paid = ok(http.post(f"/v1/admin/payouts/{payout['id']}/mark-paid", headers=ah, json={"bank_reference": "PP-E2E-1"}))
    assert paid["status"] == "paid" and paid["bank_reference"] == "PP-E2E-1"
    mine = ok(http.get("/v1/doctor/payouts", headers=world["doctor"]))
    assert payout["id"] in {p["id"] for p in mine if p["status"] == "paid"}

    # payout.paid → payment ledger: doctor_payable shu summaga kamayadi
    eventually(lambda: _assert_equal(_doctor_payable(http, ah, world["doctor_id"]), debt - payout["net_tiyin"]), timeout=60)
    assert _trial_balance(http, ah) == 0


def _assert_equal(a: object, b: object) -> None:
    assert a == b, (a, b)
