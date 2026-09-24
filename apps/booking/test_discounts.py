"""C12 — promo kod, birinchi bron, takroriy qabul; taqsimot va daftar."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking.pricing import split_with_discount
from api.booking.services import BookingRequest, InvalidBookingRequest, cancel_booking, create_booking
from apps.account.models import User
from apps.booking.models import Booking, PromoCode, PromoRedemption
from apps.schedule.models import TimeSlot

R = (Decimal("0.15"), Decimal("0.01"))


def _slot(doctor, clinic, hours=48):
    start = (timezone.now() + timedelta(hours=hours)).replace(second=0, microsecond=0)
    return TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))


def _req(client_user, patient, doctor, slot, service, promo=""):
    return BookingRequest(client_id=client_user.id, patient_id=patient.id, doctor_id=doctor.id,
                          slot_id=slot.id, service_ids=[service.id], promo_code=promo)


# ---------------------------------------------------------------------------
# Sof funksiyalar
# ---------------------------------------------------------------------------


def test_platforma_tolaydigan_chegirma_shifokor_ulushiga_tegmaydi():
    full = split_with_discount(Decimal("100000"), Decimal("0"), "", *R)
    s = split_with_discount(Decimal("100000"), Decimal("20000"), "platform", *R)
    assert s.total_price == Decimal("80000.00")
    assert s.doctor_payout == full.doctor_payout
    assert s.platform_fee + s.provider_fee + s.doctor_payout == s.total_price
    assert s.platform_fee < 0  # komissiyadan katta chegirma — marketing xarajati


def test_shifokor_tolaydigan_chegirma_odatiy_taqsimot():
    s = split_with_discount(Decimal("100000"), Decimal("50000"), "doctor", *R)
    assert s.total_price == Decimal("50000.00") and s.platform_fee == Decimal("7500.00")


# ---------------------------------------------------------------------------
# Bron oqimi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_promo_kod_qollanadi_va_limit_hisoblanadi(client_user, patient, doctor, clinic, service):
    PromoCode.objects.create(code="bahor", kind="percent", value=20, max_uses=1)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service, promo=" Bahor "))
    assert (b.original_price, b.discount_amount, b.total_price) == (Decimal("150000"), Decimal("30000.00"),
                                                                    Decimal("120000.00"))
    assert (b.discount_kind, b.promo_code, b.discount_borne_by) == ("promo", "BAHOR", "platform")
    assert b.prepay_amount == b.total_price

    other = User.objects.create_user(phone="+998901112244")
    from apps.account.models import Patient

    p2 = Patient.objects.create(owner=other, full_name="X", birth_date="1990-01-01", gender="male")
    with pytest.raises(InvalidBookingRequest, match="limiti"):
        create_booking(_req(other, p2, doctor, _slot(doctor, clinic, 50), service, promo="BAHOR"))

    # Bron bekor bo'lsa limit qaytadi
    cancel_booking(b.id, actor="client")
    assert not PromoRedemption.objects.get(booking=b).is_active
    create_booking(_req(other, p2, doctor, _slot(doctor, clinic, 52), service, promo="BAHOR"))


@pytest.mark.django_db
def test_notogri_promo_bron_yaratmaydi(client_user, patient, doctor, clinic, service):
    slot = _slot(doctor, clinic)
    with pytest.raises(InvalidBookingRequest, match="topilmadi"):
        create_booking(_req(client_user, patient, doctor, slot, service, promo="YOQ"))
    assert TimeSlot.objects.get(id=slot.id).status == TimeSlot.Status.FREE
    PromoCode.objects.create(code="ESKI", kind="fixed", value=1000, valid_until=timezone.now() - timedelta(days=1))
    with pytest.raises(InvalidBookingRequest, match="muddati"):
        create_booking(_req(client_user, patient, doctor, slot, service, promo="ESKI"))


@pytest.mark.django_db
@override_settings(PRICING={"first_booking_percent": 10})
def test_birinchi_bron_chegirmasi_faqat_bir_marta(client_user, patient, doctor, clinic, service):
    first = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert (first.discount_kind, first.discount_amount) == ("first_booking", Decimal("15000.00"))
    Booking.objects.filter(id=first.id).update(status=Booking.Status.CONFIRMED)
    second = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic, 60), service))
    assert second.discount_amount == 0


@pytest.mark.django_db
def test_takroriy_qabul_shifokor_hisobidan(pending_booking, client_user, patient, doctor, clinic, service):
    doctor.follow_up_discount_percent = 50
    doctor.save()
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.COMPLETED,
                                                          completed_at=timezone.now() - timedelta(days=3))
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert (b.discount_kind, b.discount_borne_by, b.total_price) == ("follow_up", "doctor", Decimal("75000.00"))
    assert b.platform_fee + b.provider_fee + b.doctor_payout == b.total_price


@pytest.mark.django_db
def test_chegirmalar_qoshilmaydi_eng_foydalisi(pending_booking, client_user, patient, doctor, clinic, service):
    doctor.follow_up_discount_percent = 30
    doctor.save()
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.COMPLETED, completed_at=timezone.now())
    PromoCode.objects.create(code="MINI", kind="fixed", value=5000)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service, promo="MINI"))
    assert (b.discount_kind, b.discount_amount) == ("follow_up", Decimal("45000.00"))


@pytest.mark.django_db
def test_platforma_chegirmasi_daftarda_balansda(client_user, patient, doctor, clinic, service):
    from api.payments import ledger
    from apps.payment.models import LedgerEntry, Payment

    PromoCode.objects.create(code="KATTA", kind="percent", value=50)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service, promo="KATTA"))
    p = Payment.objects.create(booking=b, provider="payme", amount=b.total_price, idempotency_key="d1")
    ledger.post_payment_succeeded(p.id)
    entries = LedgerEntry.objects.filter(transaction__booking=b)
    assert sum(e.debit for e in entries) == sum(e.credit for e in entries)
    revenue = entries.get(account=LedgerEntry.Account.PLATFORM_REVENUE)
    assert revenue.debit > 0  # marketing xarajati


@pytest.mark.django_db
def test_narxni_oldindan_korish(client_user, doctor, service):
    PromoCode.objects.create(code="TEST10", kind="percent", value=10)
    api = APIClient()
    api.force_authenticate(client_user)
    r = api.post("/api/v1/booking/price-preview",
                 {"doctor_id": str(doctor.id), "service_ids": [str(service.id)], "promo_code": "test10"}, format="json")
    assert r.json()["total_price"] == "135000.00"
    assert not PromoRedemption.objects.exists()  # limit band qilinmaydi
    bad = api.post("/api/v1/booking/price-preview",
                   {"doctor_id": str(doctor.id), "service_ids": [str(service.id)], "promo_code": "yoq"}, format="json")
    assert bad.status_code == 400


@pytest.mark.django_db
def test_bepul_takroriy_qabul_darhol_tasdiqlanadi(pending_booking, client_user, patient, doctor, clinic, service):
    doctor.follow_up_discount_percent = 100
    doctor.clinic_payment_mode = "prepaid"
    doctor.save()
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.COMPLETED, completed_at=timezone.now())
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert b.total_price == 0 and b.prepay_amount == 0
    assert b.status == Booking.Status.CONFIRMED
