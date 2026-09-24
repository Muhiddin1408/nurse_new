"""Bosqich 0 tayyorlik mezoni: begona/mos kelmaydigan ID bilan bron barcha yo'llarda bloklangan.

A1 (begona bemor/manzil), A2 (begona slot), A3/C5 (xizmat, joy, davomiylik),
A5 (Idempotency-Key), A7/C7 (parallel bron cheklovlari), C8 (vaqt zonasi),
E8 (lazy expiry).
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking.services import (
    BookingError,
    BookingRequest,
    InvalidBookingRequest,
    _generate_number,
    create_booking,
)
from apps.booking.models import Booking
from apps.schedule.models import TimeSlot


def make_slot(doctor, *, clinic=None, hours=3, minutes=30):
    start = (timezone.now() + timedelta(hours=hours)).replace(second=0, microsecond=0)
    return TimeSlot.objects.create(
        doctor=doctor, clinic=clinic, start_at=start,
        end_at=start + timedelta(minutes=minutes), status=TimeSlot.Status.FREE,
    )


def req(client_user, patient, doctor, slot, services, **kw):
    return BookingRequest(
        client_id=client_user.id, patient_id=patient.id, doctor_id=doctor.id,
        slot_id=slot.id, service_ids=[s.id for s in services], **kw,
    )


@pytest.fixture
def stranger(db):
    from apps.account.models import Patient, User

    user = User.objects.create_user(phone="+998901115555", full_name="Begona")
    patient = Patient.objects.create(
        owner=user, full_name="Begona bemor", birth_date="1985-05-05", gender="female"
    )
    return user, patient


@pytest.fixture
def other_doctor(db, specialization):
    from apps.account.models import User
    from apps.catalog.models import Doctor, Service

    user = User.objects.create_user(phone="+998901117777", full_name="Dr. B")
    doc = Doctor.objects.create(user=user, status=Doctor.Status.APPROVED)
    doc.specializations.add(specialization)
    Service.objects.create(
        doctor=doc, specialization=specialization, name="Arzon", place="clinic",
        duration_minutes=30, price=Decimal("10000"),
    )
    return doc


# --- baxtli yo'l ------------------------------------------------------------


@pytest.mark.django_db
def test_togri_bron_yaratiladi(client_user, patient, doctor, clinic, service):
    slot = make_slot(doctor, clinic=clinic)
    booking = create_booking(req(client_user, patient, doctor, slot, [service]))
    assert booking.status == Booking.Status.PENDING_PAYMENT


# --- A1 -----------------------------------------------------------------------


@pytest.mark.django_db
def test_begona_bemor_bilan_bron_400(client_user, doctor, clinic, service, stranger):
    _, foreign_patient = stranger
    slot = make_slot(doctor, clinic=clinic)
    with pytest.raises(InvalidBookingRequest, match="patient_id"):
        create_booking(req(client_user, foreign_patient, doctor, slot, [service]))
    assert TimeSlot.objects.get(id=slot.id).status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_begona_va_mavjud_bolmagan_bemor_xatosi_bir_xil(client_user, patient, doctor, clinic, service, stranger):
    import uuid

    _, foreign_patient = stranger
    slot = make_slot(doctor, clinic=clinic)
    messages = []
    for pid in (foreign_patient.id, uuid.uuid4()):
        r = req(client_user, patient, doctor, slot, [service])
        r = BookingRequest(**{**r.__dict__, "patient_id": pid})
        with pytest.raises(InvalidBookingRequest) as exc:
            create_booking(r)
        messages.append(str(exc.value))
    assert messages[0] == messages[1]


@pytest.mark.django_db
def test_begona_manzil_bilan_uy_chaqiruvi_400(client_user, patient, doctor, specialization, stranger):
    from apps.account.models import Address
    from apps.catalog.models import Service

    foreign_user, _ = stranger
    address = Address.objects.create(
        user=foreign_user, city="Toshkent", street="X", latitude=41, longitude=69
    )
    home = Service.objects.create(
        doctor=doctor, specialization=specialization, name="Uyda", place="home",
        duration_minutes=30, price=Decimal("200000"),
    )
    slot = make_slot(doctor, clinic=None)
    with pytest.raises(InvalidBookingRequest, match="address_id"):
        create_booking(req(client_user, patient, doctor, slot, [home], address_id=address.id))


# --- A2 -----------------------------------------------------------------------


@pytest.mark.django_db
def test_boshqa_shifokorning_sloti_400(client_user, patient, doctor, clinic, service, other_doctor):
    foreign_slot = make_slot(other_doctor, clinic=clinic)
    with pytest.raises(InvalidBookingRequest, match="slot_id"):
        create_booking(req(client_user, patient, doctor, foreign_slot, [service]))
    assert TimeSlot.objects.get(id=foreign_slot.id).status == TimeSlot.Status.FREE


# --- A3 / C5 ----------------------------------------------------------------


@pytest.mark.django_db
def test_begona_shifokor_xizmati_400(client_user, patient, doctor, clinic, other_doctor):
    slot = make_slot(doctor, clinic=clinic)
    cheap = other_doctor.services.first()
    with pytest.raises(InvalidBookingRequest, match="xizmatlar"):
        create_booking(req(client_user, patient, doctor, slot, [cheap]))


@pytest.mark.django_db
def test_60_daqiqalik_xizmat_30_daqiqalik_slotga_400(client_user, patient, doctor, clinic, specialization):
    from apps.catalog.models import Service

    long_service = Service.objects.create(
        doctor=doctor, specialization=specialization, name="UZI", place="clinic",
        duration_minutes=60, price=Decimal("300000"),
    )
    slot = make_slot(doctor, clinic=clinic, minutes=30)
    with pytest.raises(InvalidBookingRequest, match="daqiqa"):
        create_booking(req(client_user, patient, doctor, slot, [long_service]))


@pytest.mark.django_db
def test_uy_xizmati_klinika_slotiga_400(client_user, patient, doctor, clinic, specialization):
    from apps.account.models import Address
    from apps.catalog.models import Service

    address = Address.objects.create(user=client_user, city="Toshkent", street="Y", latitude=41, longitude=69)
    home = Service.objects.create(
        doctor=doctor, specialization=specialization, name="Uyda", place="home",
        duration_minutes=30, price=Decimal("200000"),
    )
    slot = make_slot(doctor, clinic=clinic)
    with pytest.raises(InvalidBookingRequest, match="joyiga"):
        create_booking(req(client_user, patient, doctor, slot, [home], address_id=address.id))


@pytest.mark.django_db
def test_klinika_xizmati_shifokor_ishlaydigan_klinikada_ruxsat(client_user, patient, doctor, clinic, specialization):
    from apps.catalog.models import DoctorAffiliation, Service

    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    clinic_service = Service.objects.create(
        clinic=clinic, specialization=specialization, name="Klinika konsult", place="clinic",
        duration_minutes=30, price=Decimal("120000"),
    )
    slot = make_slot(doctor, clinic=clinic)
    assert create_booking(req(client_user, patient, doctor, slot, [clinic_service]))


@pytest.mark.django_db
def test_klinika_xizmati_shifokor_ishlamaydigan_klinikada_400(client_user, patient, doctor, clinic, specialization):
    from apps.catalog.models import Service

    clinic_service = Service.objects.create(
        clinic=clinic, specialization=specialization, name="Klinika konsult", place="clinic",
        duration_minutes=30, price=Decimal("120000"),
    )
    slot = make_slot(doctor, clinic=clinic)
    with pytest.raises(InvalidBookingRequest):
        create_booking(req(client_user, patient, doctor, slot, [clinic_service]))


# --- A7 / C7 ----------------------------------------------------------------


@pytest.mark.django_db
def test_bemorda_shu_vaqtda_boshqa_qabul_409(client_user, patient, doctor, clinic, service, other_doctor):
    slot = make_slot(doctor, clinic=clinic)
    create_booking(req(client_user, patient, doctor, slot, [service]))

    parallel = TimeSlot.objects.create(
        doctor=other_doctor, clinic=clinic, start_at=slot.start_at, end_at=slot.end_at
    )
    with pytest.raises(BookingError, match="boshqa qabul"):
        create_booking(req(client_user, patient, other_doctor, parallel, [other_doctor.services.first()]))


@pytest.mark.django_db
def test_mijozda_3_tadan_ortiq_kutilayotgan_bron_409(client_user, doctor, clinic, service):
    from apps.account.models import Patient

    for i in range(4):
        p = Patient.objects.create(owner=client_user, full_name=f"P{i}", birth_date="1990-01-01", gender="male")
        slot = make_slot(doctor, clinic=clinic, hours=3 + i)
        r = req(client_user, p, doctor, slot, [service])
        if i < 3:
            create_booking(r)
        else:
            with pytest.raises(BookingError, match="oldingi bronlarni"):
                create_booking(r)


# --- E8: lazy expiry --------------------------------------------------------


@pytest.mark.django_db
def test_cron_ishlamasa_ham_muddati_otgan_hold_qayta_sotiladi(client_user, patient, doctor, clinic, service, stranger):
    """Bosqich 0 mezoni: `expire_bookings` o'chiq — slotlar baribir to'g'ri sotiladi."""
    from api.schedule.services import get_available_slots

    slot = make_slot(doctor, clinic=clinic)
    first = create_booking(req(client_user, patient, doctor, slot, [service]))
    TimeSlot.objects.filter(id=slot.id).update(hold_expires_at=timezone.now() - timedelta(minutes=1))

    # o'qishda bo'sh ko'rinadi
    assert slot.id in set(get_available_slots(doctor_id=doctor.id).values_list("id", flat=True))

    # boshqa mijoz band qila oladi, eski bron EXPIRED bo'ladi
    other_user, other_patient = stranger
    second = create_booking(req(other_user, other_patient, doctor, slot, [service]))

    first.refresh_from_db()
    assert first.status == Booking.Status.EXPIRED
    assert second.status == Booking.Status.PENDING_PAYMENT


@pytest.mark.django_db
def test_bekor_qilingan_slotni_qayta_bron_qilish_mumkin(client_user, patient, doctor, clinic, service):
    from api.booking.services import cancel_booking

    slot = make_slot(doctor, clinic=clinic)
    first = create_booking(req(client_user, patient, doctor, slot, [service]))
    cancel_booking(first.id)
    assert create_booking(req(client_user, patient, doctor, slot, [service]))


# --- C8 ---------------------------------------------------------------------


@pytest.mark.django_db
def test_bron_raqami_toshkent_sanasi_bilan():
    from datetime import datetime, timezone as dt_tz

    # UTC 2026-09-14 21:30 = Toshkent 2026-09-15 02:30
    fake_now = datetime(2026, 9, 14, 21, 30, tzinfo=dt_tz.utc)
    with mock.patch("django.utils.timezone.now", return_value=fake_now):
        assert _generate_number().startswith("MB-260915-")


# --- A5: Idempotency-Key ----------------------------------------------------


@pytest.mark.django_db
def test_idempotency_key_boshqa_tana_bilan_422_va_foydalanuvchiga_bogliq(
    client_user, patient, doctor, clinic, service, stranger
):
    api = APIClient()
    api.force_authenticate(client_user)
    slot = make_slot(doctor, clinic=clinic)
    body = {
        "patient_id": str(patient.id), "doctor_id": str(doctor.id),
        "slot_id": str(slot.id), "service_ids": [str(service.id)],
    }
    first = api.post("/api/v1/booking/bookings", body, format="json", HTTP_IDEMPOTENCY_KEY="k1")
    again = api.post("/api/v1/booking/bookings", body, format="json", HTTP_IDEMPOTENCY_KEY="k1")
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]

    changed = api.post(
        "/api/v1/booking/bookings", {**body, "comment": "boshqa"}, format="json", HTTP_IDEMPOTENCY_KEY="k1"
    )
    assert changed.status_code == 422

    # boshqa foydalanuvchi o'sha kalit bilan begona bronni OLMAYDI
    other_user, other_patient = stranger
    api.force_authenticate(other_user)
    other_slot = make_slot(doctor, clinic=clinic, hours=6)
    resp = api.post(
        "/api/v1/booking/bookings",
        {**body, "patient_id": str(other_patient.id), "slot_id": str(other_slot.id)},
        format="json", HTTP_IDEMPOTENCY_KEY="k1",
    )
    assert resp.status_code == 201
    assert resp.json()["id"] != first.json()["id"]
