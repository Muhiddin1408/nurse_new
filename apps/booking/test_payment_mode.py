"""B10 — to'lov rejimi: prepaid / deposit / at_clinic.

Rejimni MIJOZ TANLAMAYDI — u qabul joyi, shifokor sozlamasi va mijozning
no-show tarixidan kelib chiqadi. Shuning uchun sinovlarning asosiy qismi
sof funksiya (`resolve`) ustida: har bir kombinatsiya arzon yopiladi.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from api.booking.payment_mode import PaymentPlan, resolve
from api.booking.services import BookingRequest, create_booking
from apps.booking.models import Booking
from apps.schedule.models import TimeSlot

Mode = Booking.PaymentMode


def plan(**kwargs) -> PaymentPlan:
    defaults = dict(
        total=Decimal("100000"),
        is_home_visit=False,
        doctor_mode="prepaid",
        client_no_shows=0,
        deposit_percent=20,
        force_prepaid_after=2,
    )
    return resolve(**{**defaults, **kwargs})


# ---------------------------------------------------------------------------
# Rejimni aniqlash (sof funksiya)
# ---------------------------------------------------------------------------


def test_default_toliq_oldindan():
    assert plan() == PaymentPlan(Mode.PREPAID, Decimal("100000"))


def test_klinikada_tolash_onlayn_summa_nol():
    assert plan(doctor_mode="at_clinic") == PaymentPlan(Mode.AT_CLINIC, Decimal("0"))


def test_zaklad_foiz_boyicha_hisoblanadi():
    assert plan(doctor_mode="deposit").prepay_amount == Decimal("20000.00")


def test_uy_chaqiruvi_har_doim_toliq_oldindan():
    """Shifokor shahar bo'ylab yo'lga chiqadi — eng qimmat no-show turi.
    Shifokor sozlamasi bu qoidani BEKOR QILA OLMAYDI."""
    for doctor_mode in ("at_clinic", "deposit", "prepaid"):
        assert plan(is_home_visit=True, doctor_mode=doctor_mode) == PaymentPlan(
            Mode.PREPAID, Decimal("100000")
        )


def test_no_show_tarixi_yomon_mijoz_oldindan_tolaydi():
    """Klinikada to'lash — ishonch, va uni suiiste'mol qilgan uni yo'qotadi.
    Aks holda bir necha mijoz rejimni barcha shifokorlar uchun foydasiz qilardi."""
    assert plan(doctor_mode="at_clinic", client_no_shows=1).mode == Mode.AT_CLINIC
    assert plan(doctor_mode="at_clinic", client_no_shows=2).mode == Mode.PREPAID
    assert plan(doctor_mode="deposit", client_no_shows=5).mode == Mode.PREPAID


def test_juda_kichik_zaklad_klinikada_tolashga_aylanadi():
    """0 so'mlik "zaklad" — bu klinikada to'lash, faqat nomi boshqa."""
    assert plan(total=Decimal("0.01"), doctor_mode="deposit").mode == Mode.AT_CLINIC
    assert plan(total=Decimal("0"), doctor_mode="deposit").mode == Mode.AT_CLINIC


# ---------------------------------------------------------------------------
# Bron oqimi
# ---------------------------------------------------------------------------


def make_request(client_user, patient, doctor, slot, service):
    return BookingRequest(
        client_id=client_user.id,
        patient_id=patient.id,
        doctor_id=doctor.id,
        slot_id=slot.id,
        service_ids=[service.id],
    )


@pytest.fixture
def free_slot(db, doctor, clinic, service):
    start = (timezone.now() + timedelta(hours=48)).replace(second=0, microsecond=0)
    return TimeSlot.objects.create(
        doctor=doctor, clinic=clinic, start_at=start,
        end_at=start + timedelta(minutes=service.duration_minutes),
        status=TimeSlot.Status.FREE,
    )


@pytest.mark.django_db
def test_klinikada_tolanadigan_bron_darhol_tasdiqlanadi(
    client_user, patient, doctor, free_slot, service
):
    """Konversiyani yo'qotadigan qadam (to'lov sahifasi) butunlay olib tashlanadi."""
    doctor.clinic_payment_mode = "at_clinic"
    doctor.save(update_fields=["clinic_payment_mode"])

    booking = create_booking(make_request(client_user, patient, doctor, free_slot, service))

    assert booking.status == Booking.Status.CONFIRMED
    assert booking.payment_mode == Mode.AT_CLINIC
    assert booking.prepay_amount == 0
    # Hold qoldirilsa, tasdiqlangan bronning sloti 10 daqiqada bo'shab ketardi
    assert TimeSlot.objects.get(id=free_slot.id).status == TimeSlot.Status.BOOKED


@pytest.mark.django_db
def test_oldindan_tolanadigan_bron_tolov_kutadi(
    client_user, patient, doctor, free_slot, service
):
    booking = create_booking(make_request(client_user, patient, doctor, free_slot, service))

    assert booking.status == Booking.Status.PENDING_PAYMENT
    assert booking.payment_mode == Mode.PREPAID
    assert booking.prepay_amount == booking.total_price
    assert TimeSlot.objects.get(id=free_slot.id).status == TimeSlot.Status.HELD


@pytest.mark.django_db
def test_zakladda_onlayn_faqat_bir_qismi_tolanadi(
    client_user, patient, doctor, free_slot, service
):
    doctor.clinic_payment_mode = "deposit"
    doctor.save(update_fields=["clinic_payment_mode"])

    booking = create_booking(make_request(client_user, patient, doctor, free_slot, service))

    assert booking.payment_mode == Mode.DEPOSIT
    assert 0 < booking.prepay_amount < booking.total_price


@pytest.mark.django_db
def test_klinikada_tolanadigan_bronni_onlayn_tolab_bolmaydi(
    client_user, patient, doctor, free_slot, service
):
    """U allaqachon tasdiqlangan — checkout'ga umuman kelmasligi kerak."""
    from api.payments.bron import CheckoutNotAllowed, create_checkout

    doctor.clinic_payment_mode = "at_clinic"
    doctor.save(update_fields=["clinic_payment_mode"])
    booking = create_booking(make_request(client_user, patient, doctor, free_slot, service))

    with pytest.raises(CheckoutNotAllowed):
        create_checkout(booking=booking, provider="payme")


@pytest.mark.django_db
def test_zaklad_checkoutida_summa_toliq_narx_emas(
    client_user, patient, doctor, free_slot, service
):
    """Qolgan qismi klinikada to'lanadi — provayderga to'liq narx ketsa,
    mijozdan ikki marta pul olingan bo'lardi."""
    from api.payments.bron import create_checkout

    doctor.clinic_payment_mode = "deposit"
    doctor.save(update_fields=["clinic_payment_mode"])
    booking = create_booking(make_request(client_user, patient, doctor, free_slot, service))

    checkout = create_checkout(booking=booking, provider="payme")

    assert checkout.amount == int(booking.prepay_amount)
    assert checkout.amount < int(booking.total_price)
