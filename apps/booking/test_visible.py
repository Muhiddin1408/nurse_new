"""Sprint 3.4 — C15.1 paginatsiya, C15.2 soft-delete, C15.5 yaqinimdagi, C15.6 sevimlilar, C15.7 yana bron."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.account.models import Address, FavoriteDoctor, Patient
from apps.booking.models import Booking, BookingItem
from apps.catalog.models import Clinic, DoctorAffiliation
from apps.schedule.models import TimeSlot


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def _booking(n, *, client, patient, doctor, service, clinic, status=Booking.Status.COMPLETED, hours=-48):
    start = (timezone.now() + timedelta(hours=hours)).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start,
                                   end_at=start + timedelta(minutes=30), status=TimeSlot.Status.BOOKED)
    b = Booking.objects.create(number=f"MB-V-{n}", client=client, patient=patient, doctor=doctor, slot=slot,
                               status=status, total_price=service.price)
    BookingItem.objects.create(booking=b, service=service, service_name=service.name, price=service.price,
                               duration_minutes=30)
    return b


# ---------------------------------------------------------------------------
# C15.1
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_mening_bronlarim_cursor_va_filtrlar(client_user, patient, doctor, service, clinic):
    for i in range(5):
        _booking(i, client=client_user, patient=patient, doctor=doctor, service=service, clinic=clinic,
                 hours=-24 * (i + 1))
    _booking(9, client=client_user, patient=patient, doctor=doctor, service=service, clinic=clinic,
             status=Booking.Status.CANCELLED, hours=-24 * 10)
    api = api_for(client_user)

    page1 = api.get("/api/v1/booking/bookings/mine", {"page_size": 4}).json()
    assert len(page1["results"]) == 4 and page1["next"]
    page2 = api.get(page1["next"]).json()
    seen = [b["number"] for b in page1["results"] + page2["results"]]
    assert len(seen) == len(set(seen)) == 6

    only = api.get("/api/v1/booking/bookings/mine", {"status": "cancelled"}).json()["results"]
    assert [b["number"] for b in only] == ["MB-V-9"]

    day = (timezone.localtime(timezone.now()) - timedelta(days=2)).date().isoformat()
    ranged = api.get("/api/v1/booking/bookings/mine", {"date_from": day, "date_to": day}).json()["results"]
    assert [b["number"] for b in ranged] == ["MB-V-1"]

    assert api.get("/api/v1/booking/bookings/mine", {"status": "yoq"}).status_code == 400


# ---------------------------------------------------------------------------
# C15.2
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bronli_bemor_yashiriladi_bronsizi_ochiriladi(client_user, patient, doctor, service, clinic):
    _booking(1, client=client_user, patient=patient, doctor=doctor, service=service, clinic=clinic)
    lonely = Patient.objects.create(owner=client_user, full_name="Y", birth_date="1990-01-01", gender="male")
    api = api_for(client_user)

    assert api.delete(f"/api/v1/patient/patients/{patient.id}/").status_code == 204
    assert Patient.objects.get(id=patient.id).is_deleted  # tarix saqlandi
    assert api.delete(f"/api/v1/patient/patients/{lonely.id}/").status_code == 204
    assert not Patient.objects.filter(id=lonely.id).exists()
    assert api.get("/api/v1/patient/patients/").json() == []


@pytest.mark.django_db
def test_faol_bronli_bemor_ochirilmaydi(pending_booking):
    api = api_for(pending_booking.client)
    assert api.delete(f"/api/v1/patient/patients/{pending_booking.patient_id}/").status_code == 409


@pytest.mark.django_db
def test_ochirilgan_manzil_bronga_ishlatilmaydi(client_user):
    from api.patients.services import get_owned_address

    a = Address.objects.create(user=client_user, city="T", street="x", latitude=1, longitude=1, is_deleted=True)
    assert get_owned_address(client_user.id, a.id) is None


# ---------------------------------------------------------------------------
# C15.5
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_yaqinimdagi_shifokorlar_masofa_boyicha(doctor, clinic):
    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    api = APIClient()
    near = api.get("/api/v1/catalog/doctors", {"lat": "41.315", "lng": "69.240", "radius": 3}).json()
    assert [d["id"] for d in near] == [str(doctor.id)] and 0 < near[0]["distance_km"] < 1
    assert api.get("/api/v1/catalog/doctors", {"lat": "41.60", "lng": "69.24", "radius": 3}).json() == []
    assert api.get("/api/v1/catalog/doctors", {"lat": "41.3"}).status_code == 400
    assert api.get("/api/v1/catalog/doctors", {"lat": "41.3", "lng": "69.2", "radius": 500}).status_code == 400


@pytest.mark.django_db
def test_nofaol_klinika_hisobga_olinmaydi(doctor, clinic):
    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    Clinic.objects.filter(id=clinic.id).update(status=Clinic.Status.SUSPENDED)
    assert APIClient().get("/api/v1/catalog/doctors", {"lat": "41.311", "lng": "69.240"}).json() == []


# ---------------------------------------------------------------------------
# C15.6
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sevimlilar_idempotent(client_user, doctor):
    api = api_for(client_user)
    url = f"/api/v1/catalog/doctors/{doctor.id}/favorite"
    assert api.put(url).status_code == 204 and api.put(url).status_code == 204
    assert FavoriteDoctor.objects.count() == 1
    assert [d["id"] for d in api.get("/api/v1/catalog/favorites").json()] == [str(doctor.id)]
    assert api.delete(url).status_code == 204
    assert api.get("/api/v1/catalog/favorites").json() == []


@pytest.mark.django_db
def test_tasdiqlanmagan_shifokor_sevimliga_qoshilmaydi(client_user, doctor):
    doctor.status = "suspended"
    doctor.save()
    assert api_for(client_user).put(f"/api/v1/catalog/doctors/{doctor.id}/favorite").status_code == 404


# ---------------------------------------------------------------------------
# C15.7
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_yana_bron_forma_hozirgi_narx_va_bosh_vaqtlar(client_user, patient, doctor, service, clinic):
    old = _booking(1, client=client_user, patient=patient, doctor=doctor, service=service, clinic=clinic)
    service.price = Decimal("180000.00")
    service.save()
    start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
    free = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))
    TimeSlot.objects.create(doctor=doctor, clinic=None, start_at=start + timedelta(hours=1),
                            end_at=start + timedelta(hours=1, minutes=30))  # uy sloti — mos emas

    draft = api_for(client_user).get(f"/api/v1/booking/bookings/{old.id}/rebook").json()
    assert draft["patient_id"] == str(patient.id) and draft["place"] == "clinic"
    assert draft["service_ids"] == [str(service.id)]
    assert draft["services"][0]["price"] == "180000.00" and draft["services"][0]["previous_price"] == "150000.00"
    assert [s["slot_id"] for s in draft["slots"]] == [str(free.id)]


@pytest.mark.django_db
def test_begona_bronni_takrorlab_bolmaydi(client_user, patient, doctor, service, clinic):
    from apps.account.models import User

    old = _booking(1, client=client_user, patient=patient, doctor=doctor, service=service, clinic=clinic)
    stranger = User.objects.create_user(phone="+998909998877")
    assert api_for(stranger).get(f"/api/v1/booking/bookings/{old.id}/rebook").status_code == 404
