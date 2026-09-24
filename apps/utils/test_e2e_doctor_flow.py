"""Bosqich 2 "tayyor" mezoni, avtomat ko'rinishda:

    yangi shifokor `shell` ga tegmasdan ro'yxatdan o'tadi -> moderatsiya -> jadval
    -> mijoz bron qiladi va to'laydi -> shifokor qabulni yakunlaydi -> payout

Hammasi HTTP orqali, faqat tashqi chegaralar (SMS, Payme serveri) almashtirilgan.
"""

import base64
from datetime import date, timedelta
from unittest import mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.booking.models import Booking
from apps.catalog.models import Doctor
from apps.schedule.models import TimeSlot

PDF = b"%PDF-1.4\n%test\n"


def authed(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


@pytest.mark.django_db
def test_yangi_shifokor_shellsiz_birinchi_bronini_qabul_qiladi(specialization, clinic, settings, tmp_path):
    from apps.account.models import Patient, User
    from apps.catalog.models import DoctorAffiliation

    settings.PRIVATE_MEDIA_ROOT = tmp_path
    settings.PAYME_SECRET_KEY = "k"
    settings.PAYME_MERCHANT_ID = "m"
    settings.PAYOUT_MIN_AMOUNT = "1"

    # 1. Shifokor OTP bilan kiradi va onboarding'dan o'tadi
    sent = {}
    anon = APIClient()
    with mock.patch("api.account.services._send_sms", side_effect=lambda p, c: sent.update(code=c)):
        anon.post("/api/v1/accounts/auth/otp/request", {"phone": "+998901230000"}, format="json")
    tokens = anon.post("/api/v1/accounts/auth/otp/verify", {"phone": "+998901230000", "code": sent["code"]},
                       format="json").json()["tokens"]
    doc = APIClient()
    doc.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    assert doc.post("/api/v1/doctor/onboarding/start").status_code == 201
    assert doc.patch("/api/v1/doctor/onboarding/profile", {
        "full_name": "Dr. Yangi", "specialization_ids": [str(specialization.id)], "license_number": "L-1",
        "license_expires_at": (date.today() + timedelta(days=400)).isoformat(),
    }, format="json").status_code == 200
    for kind in ("diploma", "license"):
        assert doc.post("/api/v1/doctor/onboarding/documents",
                        {"kind": kind, "file": SimpleUploadedFile("f.pdf", PDF, "application/pdf")},
                        format="multipart").status_code == 201
    assert doc.post("/api/v1/doctor/onboarding/submit").json()["status"] == "pending"

    # 2. Platforma admini tasdiqlaydi
    admin = authed(User.objects.create_user(phone="+998901230099", role="platform_admin"))
    doctor_id = admin.get("/api/v1/moderation/doctors").json()[0]["id"]
    assert admin.post(f"/api/v1/moderation/doctors/{doctor_id}/approve").json()["status"] == "approved"
    doctor = Doctor.objects.get(id=doctor_id)
    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)  # klinika bog'lanishi (D7 — Bosqich 4)

    # 3. Shifokor o'z jadvali va xizmatini o'zi qo'shadi
    day = timezone.localdate() + timedelta(days=2)
    assert doc.post("/api/v1/doctor/working-rules", {
        "weekday": day.weekday(), "start_time": "09:00", "end_time": "12:00", "slot_minutes": 30,
        "valid_from": timezone.localdate().isoformat(), "clinic_id": str(clinic.id),
    }, format="json").status_code == 201
    service_id = doc.post("/api/v1/doctor/services", {
        "specialization_id": str(specialization.id), "name": "Konsultatsiya", "place": "clinic",
        "duration_minutes": 30, "price": "120000",
    }, format="json").json()["id"]

    # 4. Mijoz katalogda topadi, bron qiladi, to'laydi
    assert APIClient().get(f"/api/v1/catalog/doctors/{doctor_id}").status_code == 200
    client_user = User.objects.create_user(phone="+998901230001")
    patient = Patient.objects.create(owner=client_user, full_name="Bemor Birinchi", birth_date="1991-01-01", gender="male")
    slot = TimeSlot.objects.filter(doctor=doctor, status="free", start_at__date__gte=day).order_by("start_at").first()
    client = authed(client_user)
    booking_id = client.post("/api/v1/booking/bookings", {
        "patient_id": str(patient.id), "doctor_id": doctor_id, "slot_id": str(slot.id), "service_ids": [service_id],
    }, format="json").json()["id"]
    amount = client.post(f"/api/v1/payment/bookings/{booking_id}/checkout", {"provider": "payme"},
                         format="json").json()["amount"] * 100
    payme = APIClient()
    auth = "Basic " + base64.b64encode(b"Paycom:k").decode()
    for method, params in [
        ("CreateTransaction", {"id": "t1", "time": 1, "amount": amount, "account": {"booking_id": booking_id}}),
        ("PerformTransaction", {"id": "t1"}),
    ]:
        body = payme.post("/api/v1/payment/payme/webhook", {"id": 1, "method": method, "params": params},
                          format="json", HTTP_AUTHORIZATION=auth).json()
        assert "error" not in body, body

    # 5. Shifokor qabulni ko'radi va yakunlaydi (vaqtni qabul boshiga suramiz)
    assert Booking.objects.get(id=booking_id).status == "confirmed"
    past = timezone.now() - timedelta(minutes=10)
    TimeSlot.objects.filter(id=slot.id).update(start_at=past, end_at=past + timedelta(minutes=30))
    assert doc.get(f"/api/v1/doctor/appointments/{booking_id}").status_code == 200
    assert doc.post(f"/api/v1/doctor/appointments/{booking_id}/complete", {"note": "Sog'lom"},
                    format="json").json()["status"] == "completed"

    # 6. Daromad va payout
    from api.doctor.earnings import build_payouts

    today = timezone.localdate()
    payout = build_payouts(today - timedelta(days=6), today + timedelta(days=1))[0]
    assert payout.doctor_id == doctor.id and payout.bookings_count == 1
    assert doc.get("/api/v1/doctor/payouts").json()[0]["net_amount"] == str(payout.net_amount)
