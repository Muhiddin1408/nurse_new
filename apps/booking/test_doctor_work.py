"""Sprint 2.3 — qabullar (D4), xizmatlar va narxlar (D5), katalog hodisalari (E7)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.booking.models import Booking, BookingItem
from apps.catalog.models import ClinicMembership, Doctor, DoctorAffiliation, Service, ServicePriceHistory
from apps.payment.models import Payment, Refund
from apps.schedule.models import TimeSlot
from apps.utils.models import OutboxEvent


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


@pytest.fixture
def doc_api(doctor):
    return api_for(doctor.user)


def appointment(doctor, clinic, client_user, patient, service, *, hours=2, status=Booking.Status.CONFIRMED,
                address=None, number="MB-W-1"):
    start = (timezone.now() + timedelta(hours=hours)).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=None if address else clinic, start_at=start,
                                   end_at=start + timedelta(minutes=30), status=TimeSlot.Status.BOOKED)
    b = Booking.objects.create(number=number, client=client_user, patient=patient, doctor=doctor, slot=slot,
                               status=status, total_price=service.price, address=address)
    BookingItem.objects.create(booking=b, service=service, service_name=service.name, price=service.price,
                               duration_minutes=30)
    Payment.objects.create(booking=b, provider="payme", amount=b.total_price, status="succeeded",
                           idempotency_key=f"k-{number}")
    return b


# --- D4 ro'yxatlar va maxfiylik ---------------------------------------------


@pytest.mark.django_db
def test_bugungi_qabullar_va_telefon_faqat_qabul_kuni(doc_api, doctor, clinic, client_user, patient, service):
    today_b = appointment(doctor, clinic, client_user, patient, service, hours=1, number="MB-TODAY")
    later_b = appointment(doctor, clinic, client_user, patient, service, hours=72, number="MB-LATER")

    rows = doc_api.get("/api/v1/doctor/appointments/today").json()
    if timezone.localtime(today_b.slot.start_at).date() == timezone.localtime(timezone.now()).date():
        assert [r["number"] for r in rows] == ["MB-TODAY"]
        assert rows[0]["client_phone"] == client_user.phone

    detail = doc_api.get(f"/api/v1/doctor/appointments/{later_b.id}").json()
    assert detail["client_phone"] is None
    assert detail["contact_available_from"] is not None
    assert detail["patient"]["full_name"] == patient.full_name
    assert detail["patient"]["age"] >= 30


@pytest.mark.django_db
def test_uy_chaqiruvi_aniq_manzil_faqat_qabul_kuni(doc_api, doctor, clinic, client_user, patient, service):
    from apps.account.models import Address

    addr = Address.objects.create(user=client_user, city="Toshkent", street="Navoiy 5", entrance="2", floor="4",
                                  apartment="17", latitude=41.3, longitude=69.2)
    b = appointment(doctor, clinic, client_user, patient, service, hours=72, address=addr)
    data = doc_api.get(f"/api/v1/doctor/appointments/{b.id}").json()
    assert data["place"] == "home"
    assert data["address"]["street"] == "Navoiy 5"
    assert "apartment" not in data["address"]


@pytest.mark.django_db
def test_begona_qabul_404(doc_api, clinic, client_user, patient, service, specialization):
    from apps.account.models import User

    other = Doctor.objects.create(user=User.objects.create_user(phone="+998908880001"), status="approved")
    b = appointment(other, clinic, client_user, patient, service)
    assert doc_api.get(f"/api/v1/doctor/appointments/{b.id}").status_code == 404
    assert doc_api.post(f"/api/v1/doctor/appointments/{b.id}/cancel", {"reason": "x"}, format="json").status_code == 404


@pytest.mark.django_db
def test_tolanmagan_bron_shifokorga_korinmaydi(doc_api, doctor, clinic, client_user, patient, service):
    b = appointment(doctor, clinic, client_user, patient, service, status=Booking.Status.PENDING_PAYMENT)
    assert doc_api.get(f"/api/v1/doctor/appointments/{b.id}").status_code == 404


@pytest.mark.django_db
def test_royxat_filtr_va_oraliq_cheklovi(doc_api, doctor, clinic, client_user, patient, service):
    appointment(doctor, clinic, client_user, patient, service, hours=30, number="MB-A")
    d = timezone.localdate()
    rows = doc_api.get(f"/api/v1/doctor/appointments?from={d}&to={d + timedelta(days=3)}&status=confirmed").json()
    assert [r["number"] for r in rows] == ["MB-A"]
    assert doc_api.get(f"/api/v1/doctor/appointments?from={d}&to={d + timedelta(days=60)}").status_code == 400


# --- D4 amallar -------------------------------------------------------------


@pytest.mark.django_db
def test_boshlash_yakunlash_va_tarix(doc_api, doctor, clinic, client_user, patient, service):
    b = appointment(doctor, clinic, client_user, patient, service, hours=-0.1)
    assert doc_api.post(f"/api/v1/doctor/appointments/{b.id}/start").json()["started_at"] is not None
    resp = doc_api.post(f"/api/v1/doctor/appointments/{b.id}/complete", {"note": "Parhez, 3 kun dori"}, format="json")
    assert resp.status_code == 200
    assert (resp.json()["status"], resp.json()["note"]) == ("completed", "Parhez, 3 kun dori")

    nxt = appointment(doctor, clinic, client_user, patient, service, hours=48, number="MB-NEXT")
    history = doc_api.get(f"/api/v1/doctor/appointments/{nxt.id}").json()["patient_history"]
    assert history[0]["note"] == "Parhez, 3 kun dori"


@pytest.mark.django_db
def test_kelajak_qabulni_yakunlab_va_boshlab_bolmaydi(doc_api, doctor, clinic, client_user, patient, service):
    b = appointment(doctor, clinic, client_user, patient, service, hours=5)
    assert doc_api.post(f"/api/v1/doctor/appointments/{b.id}/start").status_code == 409
    assert doc_api.post(f"/api/v1/doctor/appointments/{b.id}/complete", {}, format="json").status_code == 409


@pytest.mark.django_db
def test_mijoz_kelmadi(doc_api, doctor, clinic, client_user, patient, service):
    b = appointment(doctor, clinic, client_user, patient, service, hours=-1)
    resp = doc_api.post(f"/api/v1/doctor/appointments/{b.id}/no-show", {}, format="json")
    assert resp.json()["status"] == "no_show"
    b.refresh_from_db()
    assert b.no_show_by == "client"
    assert not Refund.objects.exists()


@pytest.mark.django_db
def test_shifokor_bekor_qilsa_sabab_majburiy_va_100_refund(doc_api, doctor, clinic, client_user, patient, service):
    b = appointment(doctor, clinic, client_user, patient, service, hours=1)
    assert doc_api.post(f"/api/v1/doctor/appointments/{b.id}/cancel", {}, format="json").status_code == 400
    resp = doc_api.post(f"/api/v1/doctor/appointments/{b.id}/cancel", {"reason": "Kasal bo'ldim"}, format="json")
    assert resp.json()["status"] == "cancelled"
    assert Refund.objects.get(booking=b).amount == service.price  # <2 soat bo'lsa ham 100%


# --- D5 xizmatlar -----------------------------------------------------------


@pytest.mark.django_db
def test_xizmat_yaratish_va_katalog_hodisasi(doc_api, doctor, specialization):
    resp = doc_api.post("/api/v1/doctor/services", {
        "specialization_id": str(specialization.id), "name": "Uyda konsultatsiya", "place": "home",
        "duration_minutes": 40, "price": "250000",
    }, format="json")
    assert resp.status_code == 201, resp.content
    ev = OutboxEvent.objects.get(topic="catalog.events")
    assert ev.event_type == "ServicePriceChanged"
    assert ev.payload["min_price"] == 150000 or ev.payload["min_price"] == 250000


@pytest.mark.django_db
def test_xizmat_validatsiyasi(doc_api, doctor, specialization):
    from apps.catalog.models import Specialization

    other_spec = Specialization.objects.create(name="Kardiolog", slug="kardiolog")
    base = {"specialization_id": str(specialization.id), "name": "X", "place": "clinic",
            "duration_minutes": 30, "price": "100000"}
    assert doc_api.post("/api/v1/doctor/services", {**base, "specialization_id": str(other_spec.id)},
                        format="json").status_code == 400
    assert doc_api.post("/api/v1/doctor/services", {**base, "price": "0"}, format="json").status_code == 400
    assert doc_api.post("/api/v1/doctor/services", {**base, "duration_minutes": 600}, format="json").status_code == 400
    Doctor.objects.filter(id=doctor.id).update(accepts_home_visits=False)
    assert doc_api.post("/api/v1/doctor/services", {**base, "place": "home"}, format="json").status_code == 400


@pytest.mark.django_db
def test_narx_ozgarishi_tarix_snapshot_va_keskin_oshirish(doc_api, doctor, clinic, client_user, patient, service):
    b = appointment(doctor, clinic, client_user, patient, service, hours=24)

    resp = doc_api.patch(f"/api/v1/doctor/services/{service.id}", {"price": "400000"}, format="json")
    assert resp.status_code == 409 and resp.json()["code"] == "large_price_change"
    assert Service.objects.get(id=service.id).price == Decimal("150000.00")

    resp = doc_api.patch(f"/api/v1/doctor/services/{service.id}",
                         {"price": "400000", "confirm_large_change": True}, format="json")
    assert resp.status_code == 200
    h = ServicePriceHistory.objects.get(service=service)
    assert (h.old_price, h.new_price, h.is_large_change) == (Decimal("150000.00"), Decimal("400000.00"), True)
    assert h.changed_by == doctor.user

    # mavjud bron o'zgarmadi (snapshot)
    assert BookingItem.objects.get(booking=b).price == Decimal("150000.00")
    assert doc_api.get(f"/api/v1/doctor/services/{service.id}/price-history").json()[0]["new_price"] == "400000.00"
    assert OutboxEvent.objects.filter(event_type="ServicePriceChanged").count() == 1


@pytest.mark.django_db
def test_bronda_ishlatilgan_xizmat_ochirilmaydi_faqat_ochiriladi(doc_api, doctor, clinic, client_user, patient, service,
                                                                  specialization):
    appointment(doctor, clinic, client_user, patient, service)
    assert doc_api.delete(f"/api/v1/doctor/services/{service.id}").json()["result"] == "deactivated"
    assert Service.objects.get(id=service.id).is_active is False

    fresh = Service.objects.create(doctor=doctor, specialization=specialization, name="Yangi", place="clinic",
                                   duration_minutes=20, price=1000)
    assert doc_api.delete(f"/api/v1/doctor/services/{fresh.id}").json()["result"] == "deleted"
    assert doc_api.patch(f"/api/v1/doctor/services/{service.id}/toggle").json()["is_active"] is True


@pytest.mark.django_db
def test_begona_xizmatni_ozgartirib_bolmaydi(doc_api, specialization):
    from apps.account.models import User

    other = Doctor.objects.create(user=User.objects.create_user(phone="+998908880002"), status="approved")
    svc = Service.objects.create(doctor=other, specialization=specialization, name="B", place="clinic",
                                 duration_minutes=20, price=1000)
    assert doc_api.patch(f"/api/v1/doctor/services/{svc.id}", {"price": "1"}, format="json").status_code == 404


@pytest.mark.django_db
def test_ochiq_profil_moderatsiyali_maydonlarni_ozgartirmaydi(doc_api, doctor):
    assert doc_api.patch("/api/v1/doctor/profile", {"license_number": "SOXTA"}, format="json").status_code == 400
    resp = doc_api.patch("/api/v1/doctor/profile", {"bio": "Yangi bio", "home_visit_radius_km": 15}, format="json")
    assert resp.status_code == 200
    assert OutboxEvent.objects.get(topic="catalog.events").event_type == "DoctorUpdated"


# --- klinika admini ---------------------------------------------------------


@pytest.mark.django_db
def test_klinika_admini_faqat_oz_klinikasi_xizmatlari(clinic, specialization, doctor, client_user):
    from apps.catalog.models import Clinic

    ClinicMembership.objects.create(user=client_user, clinic=clinic)
    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    api = api_for(client_user)
    resp = api.post("/api/v1/clinic/services", {
        "specialization_id": str(specialization.id), "name": "UZI", "place": "clinic",
        "duration_minutes": 30, "price": "200000",
    }, format="json")
    assert resp.status_code == 201, resp.content
    # shifokor katalogida klinika xizmati ko'rinadi (C15.3) va bron qilsa bo'ladi
    names = [s["name"] for s in APIClient().get(f"/api/v1/catalog/doctors/{doctor.id}/services").json()]
    assert "UZI" in names
    # E7: klinikadagi shifokor hujjati yangilanadi
    assert OutboxEvent.objects.filter(topic="catalog.events", key=str(doctor.id)).exists()

    other = Clinic.objects.create(name="Boshqa", phone="1", city="T", street="s", latitude=1, longitude=1, status="active")
    foreign = Service.objects.create(clinic=other, specialization=specialization, name="F", place="clinic",
                                     duration_minutes=20, price=1)
    assert api.patch(f"/api/v1/clinic/services/{foreign.id}", {"price": "2"}, format="json").status_code == 404
    assert api_for(doctor.user).get("/api/v1/clinic/services").status_code == 403


# --- E7 projector -----------------------------------------------------------


def test_projector_narx_hodisasini_indeksga_yozadi():
    from unittest import mock

    from api.search.projector import _project

    es = mock.Mock()
    _project(es, {"event_type": "ServicePriceChanged", "data": {"id": "d-1", "full_name": "Dr", "min_price": 90000}})
    assert es.index.call_args.kwargs["document"]["min_price"] == 90000
