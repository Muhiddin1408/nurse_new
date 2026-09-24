"""Sprint 3.2 — Ishonch: C11 sharh/reyting, C4 uy chaqiruvi radiusi, C6 yosh ↔ mutaxassislik."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking import reviews
from api.booking.services import (
    BookingRequest,
    InvalidBookingRequest,
    age_on,
    create_booking,
)
from api.geo import haversine_km
from api.schedule.services import HOME_VISIT_BUFFER_MINUTES, travel_buffer_minutes
from apps.account.models import Address, Patient, User
from apps.booking.models import Booking, Review
from apps.catalog.models import Service
from apps.schedule.models import TimeSlot
from apps.utils.models import OutboxEvent

# Toshkent markazi va ~10 km shimoli
CENTER = (Decimal("41.311081"), Decimal("69.240562"))


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def make_slot(doctor, *, clinic=None, hours=48, minutes=30):
    start = (timezone.now() + timedelta(hours=hours)).replace(second=0, microsecond=0)
    return TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start,
                                   end_at=start + timedelta(minutes=minutes))


# ---------------------------------------------------------------------------
# C4 — sof funksiyalar
# ---------------------------------------------------------------------------


def test_haversine_malum_masofa():
    # Toshkent -> Samarqand ~ 267 km (to'g'ri chiziq)
    assert 260 < haversine_km(41.3111, 69.2406, 39.6542, 66.9597) < 275
    assert haversine_km(*CENTER, *CENTER) == 0


def test_dinamik_bufer_masofaga_proporsional():
    assert travel_buffer_minutes(None) == HOME_VISIT_BUFFER_MINUTES
    assert travel_buffer_minutes(0.5) == 15          # pastki chegara
    assert travel_buffer_minutes(5) == 25            # 10 + 5*3
    assert travel_buffer_minutes(20) == 70           # 10 + 20*3
    assert travel_buffer_minutes(100) == 90          # yuqori chegara


# ---------------------------------------------------------------------------
# C4 — bron oqimi
# ---------------------------------------------------------------------------


@pytest.fixture
def home_setup(doctor, specialization, client_user, patient):
    doctor.home_base_latitude, doctor.home_base_longitude = CENTER
    doctor.home_visit_radius_km = 10
    doctor.save()
    service = Service.objects.create(doctor=doctor, specialization=specialization, name="Uyga chaqiruv",
                                     place=Service.Place.HOME, duration_minutes=30, price=Decimal("200000"))
    return service


def _address(user, lat, lng):
    return Address.objects.create(user=user, city="Toshkent", street="x", latitude=lat, longitude=lng)


def _home_request(client_user, patient, doctor, slot, service, address):
    return BookingRequest(client_id=client_user.id, patient_id=patient.id, doctor_id=doctor.id,
                          slot_id=slot.id, service_ids=[service.id], address_id=address.id)


@pytest.mark.django_db
def test_radiusdan_tashqari_manzilga_bron_yoq(home_setup, doctor, client_user, patient):
    far = _address(client_user, Decimal("41.600000"), Decimal("69.240562"))  # ~32 km
    slot = make_slot(doctor)
    with pytest.raises(InvalidBookingRequest, match="bormaydi"):
        create_booking(_home_request(client_user, patient, doctor, slot, home_setup, far))
    assert TimeSlot.objects.get(id=slot.id).status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_yaqin_manzil_kichik_bufer_bilan_band_qilinadi(home_setup, doctor, client_user, patient):
    near = _address(client_user, Decimal("41.320000"), Decimal("69.240562"))  # ~1 km
    slot = make_slot(doctor)
    right_after = make_slot(doctor, hours=48.5)  # qabul tugashi bilan boshlanadi
    hour_later = make_slot(doctor, hours=49)
    create_booking(_home_request(client_user, patient, doctor, slot, home_setup, near))

    slot.refresh_from_db()
    assert slot.travel_buffer_minutes == 15
    assert TimeSlot.objects.get(id=right_after.id).status == TimeSlot.Status.BLOCKED
    # Qat'iy 30 daqiqalik buferda bu slot ham yopilardi; 1 km uchun 15 daqiqa yetadi
    assert TimeSlot.objects.get(id=hour_later.id).status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_bufer_saqlangan_qiymat_bilan_ochiladi(home_setup, doctor, client_user, patient):
    """Katta bufer bilan yopilgan qo'shnilar bekor qilishda ham O'SHA bufer bilan ochiladi."""
    from api.schedule.services import hold_slot, release_slot

    slot = make_slot(doctor)
    neighbour = make_slot(doctor, hours=48 + 70 / 60)  # 70 daqiqadan keyin
    hold_slot(slot.id, buffer_minutes=70)
    assert TimeSlot.objects.get(id=neighbour.id).status == TimeSlot.Status.BLOCKED

    release_slot(slot.id)
    assert TimeSlot.objects.get(id=neighbour.id).status == TimeSlot.Status.FREE
    assert TimeSlot.objects.get(id=slot.id).travel_buffer_minutes is None


@pytest.mark.django_db
def test_baza_kiritilmagan_shifokor_radius_tekshirmaydi(doctor, specialization, client_user, patient):
    service = Service.objects.create(doctor=doctor, specialization=specialization, name="Uy",
                                     place=Service.Place.HOME, duration_minutes=30, price=Decimal("1"))
    far = _address(client_user, Decimal("39.654200"), Decimal("66.959700"))
    slot = make_slot(doctor)
    create_booking(_home_request(client_user, patient, doctor, slot, service, far))
    slot.refresh_from_db()
    assert slot.travel_buffer_minutes == HOME_VISIT_BUFFER_MINUTES


@pytest.mark.django_db
def test_katalog_faqat_yetib_boradiganlarni_korsatadi(home_setup, doctor):
    api = APIClient()
    near = api.get("/api/v1/catalog/doctors", {"home_only": 1, "lat": "41.32", "lng": "69.24"})
    far = api.get("/api/v1/catalog/doctors", {"home_only": 1, "lat": "41.60", "lng": "69.24"})
    assert [d["id"] for d in near.json()] == [str(doctor.id)]
    assert far.json() == []
    assert api.get("/api/v1/catalog/doctors", {"lat": "abc"}).status_code == 400


# ---------------------------------------------------------------------------
# C6 — yosh ↔ mutaxassislik
# ---------------------------------------------------------------------------


def test_yosh_qabul_kuniga_hisoblanadi():
    assert age_on(date(2008, 5, 10), date(2026, 5, 9)) == 17
    assert age_on(date(2008, 5, 10), date(2026, 5, 10)) == 18


def _patient(owner, birth, gender="male"):
    return Patient.objects.create(owner=owner, full_name="B", birth_date=birth, gender=gender)


def _clinic_request(client_user, patient, doctor, slot, service):
    return BookingRequest(client_id=client_user.id, patient_id=patient.id, doctor_id=doctor.id,
                          slot_id=slot.id, service_ids=[service.id])


@pytest.mark.django_db
def test_bola_kattalar_terapevtiga_yozilmaydi(doctor, clinic, service, client_user):
    child = _patient(client_user, date.today() - timedelta(days=365 * 3))
    with pytest.raises(InvalidBookingRequest, match="bolalarni qabul qilmaydi"):
        create_booking(_clinic_request(client_user, child, doctor, make_slot(doctor, clinic=clinic), service))


@pytest.mark.django_db
def test_bolalarni_qabul_qiladigan_mutaxassislik(doctor, clinic, service, specialization, client_user):
    specialization.accepts_children = True
    specialization.save()
    child = _patient(client_user, date.today() - timedelta(days=365 * 3))
    create_booking(_clinic_request(client_user, child, doctor, make_slot(doctor, clinic=clinic), service))


@pytest.mark.django_db
def test_kattalar_pediatrga_yozilmaydi(doctor, clinic, service, specialization, patient, client_user):
    specialization.is_pediatric = True
    specialization.save()
    with pytest.raises(InvalidBookingRequest, match="faqat bolalar"):
        create_booking(_clinic_request(client_user, patient, doctor, make_slot(doctor, clinic=clinic), service))


@pytest.mark.django_db
def test_jins_va_yosh_chegaralari(doctor, clinic, service, specialization, patient, client_user):
    specialization.allowed_gender = "female"
    specialization.save()
    with pytest.raises(InvalidBookingRequest, match="jinsiga"):
        create_booking(_clinic_request(client_user, patient, doctor, make_slot(doctor, clinic=clinic), service))

    specialization.allowed_gender = ""
    specialization.max_patient_age = 30
    specialization.save()
    with pytest.raises(InvalidBookingRequest, match="ko'pi bilan 30"):
        create_booking(_clinic_request(client_user, patient, doctor, make_slot(doctor, clinic=clinic, hours=50), service))


# ---------------------------------------------------------------------------
# C11 — sharh va reyting
# ---------------------------------------------------------------------------


def test_avtomatik_moderatsiya_filtri():
    assert reviews.moderation_flags("Juda yaxshi shifokor, rahmat!") == []
    assert reviews.moderation_flags("Qo'ng'iroq qiling +998 90 123 45 67") == ["phone"]
    assert reviews.moderation_flags("Batafsil t.me/reklama") == ["link"]
    assert reviews.moderation_flags("ahmoq doktor") == ["profanity"]


def test_kam_sharhda_reyting_korsatilmaydi():
    assert reviews.display_rating(Decimal("5.00"), 1) is None
    assert reviews.display_rating(Decimal("4.20"), 5) == Decimal("4.20")


@pytest.fixture
def completed_booking(pending_booking):
    Booking.objects.filter(id=pending_booking.id).update(
        status=Booking.Status.COMPLETED, completed_at=timezone.now()
    )
    pending_booking.refresh_from_db()
    return pending_booking


@pytest.mark.django_db
def test_sharh_faqat_yakunlangan_bronga(pending_booking, client_user):
    with pytest.raises(reviews.ReviewError, match="yakunlangan"):
        reviews.create_review(client_id=client_user.id, booking_id=pending_booking.id,
                              data=reviews.ReviewInput(rating=5))


@pytest.mark.django_db
def test_sharh_reytingni_yangilaydi_va_shifokorga_xabar(completed_booking, client_user, doctor):
    review = reviews.create_review(client_id=client_user.id, booking_id=completed_booking.id,
                                   data=reviews.ReviewInput(rating=4, comment="Yaxshi"))
    assert review.status == Review.Status.PUBLISHED
    doctor.refresh_from_db()
    assert (doctor.rating, doctor.reviews_count) == (Decimal("4.00"), 1)
    assert OutboxEvent.objects.filter(event_type="ReviewPublished").exists()
    assert OutboxEvent.objects.filter(event_type="DoctorRatingChanged").exists()

    with pytest.raises(reviews.ReviewError, match="allaqachon"):
        reviews.create_review(client_id=client_user.id, booking_id=completed_booking.id,
                              data=reviews.ReviewInput(rating=1))


@pytest.mark.django_db
def test_begona_bronga_sharh_yoq(completed_booking):
    stranger = User.objects.create_user(phone="+998901119999")
    with pytest.raises(reviews.ReviewNotFound):
        reviews.create_review(client_id=stranger.id, booking_id=completed_booking.id,
                              data=reviews.ReviewInput(rating=5))


@pytest.mark.django_db
def test_30_kundan_keyin_sharh_yoq(completed_booking, client_user):
    Booking.objects.filter(id=completed_booking.id).update(completed_at=timezone.now() - timedelta(days=31))
    with pytest.raises(reviews.ReviewError, match="muddati"):
        reviews.create_review(client_id=client_user.id, booking_id=completed_booking.id,
                              data=reviews.ReviewInput(rating=5))


@pytest.mark.django_db
def test_shubhali_sharh_moderatsiyaga_tushadi(completed_booking, client_user, doctor):
    review = reviews.create_review(client_id=client_user.id, booking_id=completed_booking.id,
                                   data=reviews.ReviewInput(rating=1, comment="Menga yozing t.me/kanal"))
    assert review.status == Review.Status.PENDING
    doctor.refresh_from_db()
    assert doctor.reviews_count == 0  # moderatsiyagacha reytingga ta'sir yo'q

    admin = User.objects.create_user(phone="+998900000088", role="platform_admin")
    api = api_for(admin)
    assert [r["id"] for r in api.get("/api/v1/moderation/reviews").json()] == [str(review.id)]
    assert api.post(f"/api/v1/moderation/reviews/{review.id}/approve").status_code == 200
    doctor.refresh_from_db()
    assert doctor.reviews_count == 1
    assert api.post(f"/api/v1/moderation/reviews/{review.id}/reject").status_code == 409


@pytest.mark.django_db
def test_api_oqimi_sharh_javob_va_ochiq_royxat(completed_booking, client_user, doctor):
    r = api_for(client_user).post(f"/api/v1/booking/bookings/{completed_booking.id}/review",
                                  {"rating": 5, "comment": "Zo'r", "is_anonymous": True}, format="json")
    assert r.status_code == 201, r.content
    review_id = r.json()["id"]

    doc_api = api_for(doctor.user)
    assert doc_api.post(f"/api/v1/doctor/reviews/{review_id}/reply", {"text": "Rahmat!"}).status_code == 200
    assert doc_api.post(f"/api/v1/doctor/reviews/{review_id}/reply", {"text": "Yana"}).status_code == 409

    public = APIClient().get(f"/api/v1/catalog/doctors/{doctor.id}/reviews").json()
    assert public[0]["author"] is None  # anonim
    assert public[0]["doctor_reply"] == "Rahmat!"

    card = APIClient().get(f"/api/v1/catalog/doctors/{doctor.id}").json()
    assert card["rating"] is None and card["is_new"] is True  # 1 ta sharh — "Yangi shifokor"
