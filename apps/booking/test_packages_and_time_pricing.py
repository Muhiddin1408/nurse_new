"""C12 (qolgan qismi) — paketlar va dinamik narx."""

from datetime import time, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.utils import timezone

from api.booking import packages, time_pricing
from api.booking.services import BookingRequest, cancel_booking, create_booking
from api.schedule.services import LOCAL_TZ
from apps.booking.models import Booking, PackageEnrollment, PackageUse
from apps.catalog.models import PriceRule, ServicePackage
from apps.schedule.models import TimeSlot


@pytest.fixture
def auth_client(client_user):
    from rest_framework.test import APIClient

    api = APIClient()
    api.force_authenticate(client_user)
    return api


def _slot(doctor, clinic, *, at_hour=None, hours=48):
    """`at_hour` berilsa — MAHALLIY vaqtda aynan shu soatdagi slot."""
    start = (timezone.now() + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)
    if at_hour is not None:
        local = start.astimezone(LOCAL_TZ).replace(hour=at_hour)
        start = local.astimezone(dt_timezone.utc)
    return TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start,
                                   end_at=start + timedelta(minutes=30))


def _req(client_user, patient, doctor, slot, service):
    return BookingRequest(client_id=client_user.id, patient_id=patient.id, doctor_id=doctor.id,
                          slot_id=slot.id, service_ids=[service.id])


# ---------------------------------------------------------------------------
# Dinamik narx — sof funksiyalar
# ---------------------------------------------------------------------------


def test_tuzatmasiz_narx_bitta_tiyinga_ham_ozgarmaydi():
    assert time_pricing.adjust_price(Decimal("150000.33"), 0) == Decimal("150000.33")


def test_manfiy_foiz_arzonlashtiradi_musbat_qimmatlashtiradi():
    assert time_pricing.adjust_price(Decimal("100000"), -15) == Decimal("85000.00")
    assert time_pricing.adjust_price(Decimal("100000"), 20) == Decimal("120000.00")


def test_haddan_tashqari_foiz_chegaralanadi_narx_manfiy_bolmaydi():
    assert time_pricing.adjust_price(Decimal("100000"), -900) == Decimal("10000.00")
    assert time_pricing.adjust_price(Decimal("100000"), 500) == Decimal("200000.00")


def test_yarim_tundan_otadigan_oraliq():
    rule = PriceRule(start_time=time(22, 0), end_time=time(6, 0), percent=10)
    day = timezone.now().replace(hour=23, minute=0)
    assert rule.matches(day)
    assert rule.matches(day.replace(hour=2))
    assert not rule.matches(day.replace(hour=12))


def test_hafta_kuni_maskasi():
    day = timezone.now().replace(hour=9)
    weekday = str(day.weekday())
    assert PriceRule(weekdays=weekday, start_time=time(8), end_time=time(10), percent=-5).matches(day)
    other = "".join(c for c in "0123456" if c != weekday)
    assert not PriceRule(weekdays=other, start_time=time(8), end_time=time(10), percent=-5).matches(day)


# ---------------------------------------------------------------------------
# Dinamik narx — bron oqimi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ertalabki_slot_arzon_va_snapshot_item_narxiga_tushadi(client_user, patient, doctor, clinic, service):
    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=-20)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic, at_hour=8), service))
    assert b.price_adjust_percent == -20
    assert b.original_price == Decimal("120000.00")   # 150 000 - 20%
    assert b.total_price == Decimal("120000.00")
    # Snapshot ham tuzatilgan narxda — fiskal chek shundan chiqadi
    assert b.items.first().price == Decimal("120000.00")


@pytest.mark.django_db
def test_qoida_tegmaydigan_soat_bazaviy_narx(client_user, patient, doctor, clinic, service):
    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=-20)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic, at_hour=15), service))
    assert (b.price_adjust_percent, b.original_price) == (0, Decimal("150000.00"))


@pytest.mark.django_db
def test_bir_xil_prioritetda_mijoz_foydasiga(doctor):
    from api.catalog import services as catalog_services

    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=10, priority=1)
    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=-20, priority=1)
    dt = timezone.now().astimezone(LOCAL_TZ).replace(hour=9)
    assert catalog_services.get_price_adjust_percent(doctor.id, dt) == -20


@pytest.mark.django_db
def test_kichik_prioritet_yutadi(doctor):
    from api.catalog import services as catalog_services

    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=15, priority=1)
    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=-30, priority=5)
    dt = timezone.now().astimezone(LOCAL_TZ).replace(hour=9)
    assert catalog_services.get_price_adjust_percent(doctor.id, dt) == 15


# ---------------------------------------------------------------------------
# Paketlar
# ---------------------------------------------------------------------------


def _package(doctor, service, sessions=3, percent=10, days=90):
    return ServicePackage.objects.create(doctor=doctor, service=service, name="Kurs",
                                         sessions=sessions, discount_percent=percent, validity_days=days)


@pytest.mark.django_db
def test_paketga_yozilish_shartlarni_muzlatadi(client_user, doctor, service):
    pkg = _package(doctor, service, percent=10)
    enr = packages.enroll(client_id=client_user.id, package_id=pkg.id)
    ServicePackage.objects.filter(id=pkg.id).update(discount_percent=1, is_active=False)
    saved = PackageEnrollment.objects.get(id=enr.id)
    assert (saved.discount_percent, saved.sessions_total, saved.sessions_left) == (10, 3, 3)


@pytest.mark.django_db
def test_bir_xizmatga_ikkinchi_faol_paket_bolmaydi(client_user, doctor, service):
    pkg = _package(doctor, service)
    packages.enroll(client_id=client_user.id, package_id=pkg.id)
    with pytest.raises(packages.PackageError):
        packages.enroll(client_id=client_user.id, package_id=pkg.id)


@pytest.mark.django_db
def test_paket_chegirmasi_qollanadi_va_seans_yechiladi(client_user, patient, doctor, clinic, service):
    pkg = _package(doctor, service, sessions=3, percent=10)
    enr_dto = packages.enroll(client_id=client_user.id, package_id=pkg.id)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert (b.discount_kind, b.discount_borne_by) == ("package", "doctor")
    assert b.discount_amount == Decimal("15000.00")
    assert b.total_price == Decimal("135000.00")
    assert PackageEnrollment.objects.get(id=enr_dto.id).sessions_left == 2
    # Shifokor ko'taradi — taqsimot chegirmali narxdan
    assert b.platform_fee + b.provider_fee + b.doctor_payout == b.total_price


@pytest.mark.django_db
def test_bron_bekor_qilinsa_seans_qaytadi(client_user, patient, doctor, clinic, service):
    pkg = _package(doctor, service, sessions=2)
    enr_dto = packages.enroll(client_id=client_user.id, package_id=pkg.id)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert PackageEnrollment.objects.get(id=enr_dto.id).sessions_left == 1
    cancel_booking(b.id, actor=Booking.Actor.CLIENT)
    assert PackageEnrollment.objects.get(id=enr_dto.id).sessions_left == 2
    assert not PackageUse.objects.get(booking=b).is_active


@pytest.mark.django_db
def test_seanslar_tugaganda_kurs_yopiladi(client_user, patient, doctor, clinic, service):
    pkg = _package(doctor, service, sessions=1)
    enr_dto = packages.enroll(client_id=client_user.id, package_id=pkg.id)
    create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert not PackageEnrollment.objects.get(id=enr_dto.id).is_active
    # Kurs yopilgani yangi paketga yozilishni bloklamaydi
    packages.enroll(client_id=client_user.id, package_id=pkg.id)


@pytest.mark.django_db
def test_muddati_otgan_kurs_chegirma_bermaydi(client_user, patient, doctor, clinic, service):
    pkg = _package(doctor, service)
    enr_dto = packages.enroll(client_id=client_user.id, package_id=pkg.id)
    PackageEnrollment.objects.filter(id=enr_dto.id).update(expires_at=timezone.now() - timedelta(days=1))
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert b.discount_kind == "" and b.total_price == Decimal("150000.00")


@pytest.mark.django_db
def test_dinamik_narx_va_paket_ketma_ket_qollanadi(client_user, patient, doctor, clinic, service):
    """Tuzatma AVVAL, chegirma KEYIN — "arzon soat" ikki marta hisoblanmaydi."""
    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=-20)
    packages.enroll(client_id=client_user.id, package_id=_package(doctor, service, percent=10).id)
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic, at_hour=8), service))
    assert b.original_price == Decimal("120000.00")      # 150 000 - 20%
    assert b.discount_amount == Decimal("12000.00")      # 120 000 ning 10%i
    assert b.total_price == Decimal("108000.00")


@pytest.mark.django_db
def test_faqat_eng_foydali_chegirma(client_user, patient, doctor, clinic, service):
    """Paket 10%, takroriy qabul 30% — biri tanlanadi, qo'shilmaydi."""
    from apps.catalog.models import Doctor

    packages.enroll(client_id=client_user.id, package_id=_package(doctor, service, percent=10).id)
    Doctor.objects.filter(id=doctor.id).update(follow_up_discount_percent=30)
    Booking.objects.create(
        number="TEST-FU", client=client_user, patient=patient, doctor=doctor,
        slot=_slot(doctor, clinic, hours=200), status=Booking.Status.COMPLETED,
        completed_at=timezone.now(), total_price=Decimal("150000"),
    )
    b = create_booking(_req(client_user, patient, doctor, _slot(doctor, clinic), service))
    assert b.discount_kind == "follow_up"
    assert b.discount_amount == Decimal("45000.00")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_price_preview_slot_bilan_dinamik_narxni_korsatadi(auth_client, client_user, doctor, clinic, service):
    PriceRule.objects.create(doctor=doctor, start_time=time(8), end_time=time(10), percent=-20)
    slot = _slot(doctor, clinic, at_hour=8)
    r = auth_client.post("/api/v1/booking/price-preview", {
        "doctor_id": str(doctor.id), "service_ids": [str(service.id)], "slot_id": str(slot.id),
    }, format="json")
    assert r.status_code == 200
    assert r.data["price_adjust_percent"] == -20
    assert r.data["original_price"] == "120000.00"


@pytest.mark.django_db
def test_paket_endpointlari(auth_client, client_user, doctor, service):
    pkg = _package(doctor, service)
    r = auth_client.get(f"/api/v1/booking/doctors/{doctor.id}/packages")
    assert r.status_code == 200 and len(r.data) == 1

    r = auth_client.post(f"/api/v1/booking/packages/{pkg.id}/enroll")
    assert r.status_code == 201 and r.data["sessions_left"] == 3

    r = auth_client.get("/api/v1/booking/packages/mine")
    assert r.status_code == 200 and len(r.data) == 1

    # Takroran — 400, jim dublikat emas
    assert auth_client.post(f"/api/v1/booking/packages/{pkg.id}/enroll").status_code == 400
