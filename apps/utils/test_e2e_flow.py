"""Bosqich 0 natijasi, avtomat ko'rinishda (hujjat: "Bosqich 0 natijasi").

    mijoz OTP bilan kiradi -> shifokor topadi -> bron qiladi -> TO'LAYDI
    -> SMS oladi -> 15 daqiqadan keyin qayta OTP so'ramaydi (refresh)

Hammasi HTTP orqali, haqiqiy URL'lar bilan. Tashqi tizimlar o'rnida faqat
chegaralar almashtirilgan: SMS provayderi (kod ushlanadi), Payme (uning
serveri yuboradigan JSON-RPC so'rovlari aynan shu formatda yuboriladi),
Kafka (outbox hodisasi consumer'ga to'g'ridan-to'g'ri beriladi).

Real telefon + Payme test muhiti bilan qo'lda tekshiruv baribir shart —
bu test uning o'rnini bosmaydi, lekin kod tomonidan oqim uzilmaganini kafolatlaydi.
"""

import base64
import json
from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

PHONE = "+998901234567"


class FakeMsg:
    def __init__(self, envelope):
        self._value = json.dumps(envelope).encode()

    def value(self):
        return self._value

    def error(self):
        return None

    def topic(self):
        return "booking.events"

    def partition(self):
        return 0

    def offset(self):
        return 0


def payme(api, key, method, params, rpc_id=1):
    resp = api.post(
        "/api/v1/payment/payme/webhook",
        {"id": rpc_id, "method": method, "params": params},
        format="json",
        HTTP_AUTHORIZATION="Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in body, body
    return body["result"]


@pytest.mark.django_db
def test_otp_bron_tolov_sms_refresh_toliq_oqim(doctor, clinic, service, settings):
    from apps.booking.models import Booking
    from apps.schedule.models import TimeSlot
    from apps.utils.models import OutboxEvent

    settings.PAYME_SECRET_KEY = "sandbox-key"
    settings.PAYME_MERCHANT_ID = "merchant-1"
    api = APIClient()

    # 1. OTP
    sent = {}
    with mock.patch("api.account.services._send_sms", side_effect=lambda p, c: sent.update(phone=p, code=c)):
        assert api.post("/api/v1/accounts/auth/otp/request", {"phone": PHONE}, format="json").status_code == 200
    assert sent["phone"] == PHONE

    resp = api.post("/api/v1/accounts/auth/otp/verify", {"phone": PHONE, "code": sent["code"]}, format="json")
    assert resp.status_code == 200
    tokens = resp.json()["tokens"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    # 2. Bemor
    resp = api.post(
        "/api/v1/patient/patients/",
        {"full_name": "Ali Valiyev", "relation": "O'zim", "birth_date": "1990-03-01", "gender": "male"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    patient_id = resp.json()["id"]

    # 3. Shifokor va bo'sh vaqt
    start = (timezone.now() + timedelta(hours=4)).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))
    resp = api.get(f"/api/v1/schedule/doctors/{doctor.id}/slots")
    assert resp.status_code == 200
    assert str(slot.id) in resp.content.decode()

    # 4. Bron
    resp = api.post(
        "/api/v1/booking/bookings",
        {"patient_id": patient_id, "doctor_id": str(doctor.id), "slot_id": str(slot.id),
         "service_ids": [str(service.id)]},
        format="json",
        HTTP_IDEMPOTENCY_KEY="e2e-1",
    )
    assert resp.status_code == 201, resp.content
    booking_id = resp.json()["id"]

    # 5. Checkout
    resp = api.post(f"/api/v1/payment/bookings/{booking_id}/checkout", {"provider": "payme"}, format="json")
    assert resp.status_code == 200
    amount = resp.json()["amount"] * 100
    account = {"booking_id": booking_id}

    # 6. Payme serveri chaqiruvlari (sandbox ketma-ketligi)
    payme_api = APIClient()
    assert payme(payme_api, "sandbox-key", "CheckPerformTransaction", {"amount": amount, "account": account})["allow"] is True
    now_ms = int(timezone.now().timestamp() * 1000)
    created = payme(payme_api, "sandbox-key", "CreateTransaction",
                    {"id": "sbx-1", "time": now_ms, "amount": amount, "account": account})
    assert created["state"] == 1
    performed = payme(payme_api, "sandbox-key", "PerformTransaction", {"id": "sbx-1"})
    assert performed["state"] == 2
    assert payme(payme_api, "sandbox-key", "CheckTransaction", {"id": "sbx-1"})["state"] == 2

    booking = Booking.objects.get(id=booking_id)
    assert booking.status == Booking.Status.CONFIRMED

    # 7. SMS: outbox hodisasi consumer orqali
    from api.notifications import consumer

    ev = OutboxEvent.objects.get(event_type="BookingConfirmed")
    envelope = {"event_id": str(ev.id), "event_type": ev.event_type,
                "occurred_at": ev.created_at.isoformat(), "data": ev.payload}
    with mock.patch("api.notifications.services.send_sms") as send_sms:
        consumer._process_message(FakeMsg(envelope))
    calls = {c.kwargs["template"]: c.kwargs for c in send_sms.call_args_list}
    assert calls["booking_confirmed"]["phone"] == PHONE
    assert calls["booking_confirmed"]["params"]["number"] == booking.number
    # D9: shifokor ham xabar oldi (Telegram ulanmagan — SMS zaxirasi)
    assert calls["doctor_new_booking"]["phone"] == doctor.user.phone

    # 8. Access eskirdi -> refresh bilan davom, qayta OTP shart emas
    resp = APIClient().post("/api/v1/accounts/auth/token/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert resp.status_code == 200
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.json()['access']}")
    assert api.get(f"/api/v1/booking/bookings/{booking_id}").json()["status"] == "confirmed"
