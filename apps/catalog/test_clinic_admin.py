"""D7 klinika admini va D8 xonalar."""

from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from api.schedule.services import LOCAL_TZ, generate_slots
from apps.account.models import User
from apps.booking.models import Booking
from apps.catalog.models import Clinic, ClinicMembership, Doctor, DoctorAffiliation, Room
from apps.schedule.models import TimeSlot, WorkingRule
from apps.utils.models import AuditLog, OutboxEvent


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


@pytest.fixture
def admin_api(clinic):
    user = User.objects.create_user(phone="+998901230001", full_name="Klinika Admin")
    ClinicMembership.objects.create(user=user, clinic=clinic)
    return api_for(user)


def _tomorrow() -> date:
    return timezone.localtime(timezone.now(), LOCAL_TZ).date() + timedelta(days=1)


def _rule(doctor, clinic, day: date, **kw):
    return WorkingRule.objects.create(doctor=doctor, clinic=clinic, weekday=day.weekday(), start_time=time(9),
                                      end_time=time(11), slot_minutes=30, valid_from=day - timedelta(days=1),
                                      valid_to=day, **kw)


# ---------------------------------------------------------------------------
# D7 — ikki tomonlama taklif
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_taklif_shifokor_qabul_qilmaguncha_faol_emas(admin_api, doctor, clinic):
    r = admin_api.post("/api/v1/clinic/doctors/invite", {"phone": doctor.user.phone, "position": "Terapevt"},
                       format="json")
    assert r.status_code == 201 and r.json()["status"] == "invited"
    aff = DoctorAffiliation.objects.get(doctor=doctor, clinic=clinic)
    assert not aff.is_active
    assert OutboxEvent.objects.filter(event_type="ClinicInviteSent").exists()
    assert admin_api.post("/api/v1/clinic/doctors/invite", {"phone": doctor.user.phone}, format="json").status_code == 409

    doc_api = api_for(doctor.user)
    invites = doc_api.get("/api/v1/doctor/clinic-invites").json()
    assert [i["affiliation_id"] for i in invites] == [str(aff.id)]
    assert doc_api.post(f"/api/v1/doctor/clinic-invites/{aff.id}/accept").json()["status"] == "active"
    aff.refresh_from_db()
    assert aff.is_active and aff.responded_at
    assert AuditLog.objects.filter(action="doctor.invite.accept").exists()


@pytest.mark.django_db
def test_notanish_raqamga_taklif_404(admin_api):
    assert admin_api.post("/api/v1/clinic/doctors/invite", {"phone": "+998900000000"}, format="json").status_code == 404


@pytest.mark.django_db
def test_pauza_bosh_slotlarni_yopadi_resume_qaytaradi(admin_api, doctor, clinic):
    aff = DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    day = _tomorrow()
    _rule(doctor, clinic, day)
    generate_slots(doctor.id)
    assert TimeSlot.objects.filter(doctor=doctor, status="free").count() == 4

    r = admin_api.post(f"/api/v1/clinic/doctors/{aff.id}/pause")
    assert r.json()["status"] == "paused"
    assert TimeSlot.objects.filter(doctor=doctor, status="free").count() == 0
    admin_api.post(f"/api/v1/clinic/doctors/{aff.id}/resume")
    assert TimeSlot.objects.filter(doctor=doctor, status="free").count() == 4
    assert admin_api.post(f"/api/v1/clinic/doctors/{aff.id}/resume").status_code == 409


@pytest.mark.django_db
def test_begona_klinika_404(admin_api, doctor):
    other = Clinic.objects.create(name="B", phone="1", city="T", street="x", latitude=1, longitude=1, status="active")
    aff = DoctorAffiliation.objects.create(doctor=doctor, clinic=other)
    assert admin_api.post(f"/api/v1/clinic/doctors/{aff.id}/pause").status_code == 404
    assert admin_api.get(f"/api/v1/clinic/profile?clinic_id={other.id}").status_code == 404


@pytest.mark.django_db
def test_profil_faqat_ruxsat_etilgan_maydonlar(admin_api):
    ok = admin_api.patch("/api/v1/clinic/profile",
                         {"description": "Yangi", "working_hours": {"0": ["08:00", "18:00"], "6": None}}, format="json")
    assert ok.status_code == 200 and ok.json()["working_hours"]["0"] == ["08:00", "18:00"]
    assert admin_api.patch("/api/v1/clinic/profile", {"name": "Boshqa"}, format="json").status_code == 409
    assert admin_api.patch("/api/v1/clinic/profile", {"working_hours": {"0": ["18:00", "08:00"]}},
                           format="json").status_code == 409


# ---------------------------------------------------------------------------
# D7 — jadval, dam olish, qabullar, hisobot
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_dam_olish_kuni_slotlarni_yopadi_va_generatsiya_qilmaydi(admin_api, doctor, clinic):
    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    day = _tomorrow() + timedelta(days=1)
    _rule(doctor, clinic, day)
    generate_slots(doctor.id)
    r = admin_api.post("/api/v1/clinic/closures", {"date": day.isoformat(), "reason": "Bayram"}, format="json")
    assert r.status_code == 201 and r.json()["bookings_to_reschedule"] == []
    assert TimeSlot.objects.filter(doctor=doctor, status="free").count() == 0

    TimeSlot.objects.filter(doctor=doctor).delete()
    generate_slots(doctor.id)
    assert not TimeSlot.objects.filter(doctor=doctor).exists()  # yopiq kunga yaratilmaydi

    admin_api.delete(f"/api/v1/clinic/closures/{r.json()['id']}")
    assert TimeSlot.objects.filter(doctor=doctor, status="free").count() == 4

    overview = admin_api.get(f"/api/v1/clinic/schedule?date={day.isoformat()}").json()
    assert overview[0]["doctor_id"] == str(doctor.id) and len(overview[0]["slots"]) == 4


@pytest.mark.django_db
def test_reception_qidiruvi_va_hisobot(admin_api, pending_booking, clinic):
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.COMPLETED)
    day = timezone.localtime(pending_booking.slot.start_at, LOCAL_TZ).date()
    by_phone = admin_api.get(f"/api/v1/clinic/appointments?date={day}&q={pending_booking.client.phone[-7:]}").json()
    assert [a["number"] for a in by_phone] == [pending_booking.number]
    assert admin_api.get(f"/api/v1/clinic/appointments?date={day}&q=yoq").json() == []

    rep = admin_api.get(f"/api/v1/clinic/reports?date_from={day}&date_to={day}").json()
    assert Decimal(rep["total"]["revenue"]) == pending_booking.total_price
    assert rep["doctors"][0]["doctor_id"] == str(pending_booking.doctor_id)


# ---------------------------------------------------------------------------
# D8 — xonalar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_xona_ikki_shifokorga_bir_vaqtda_berilmaydi(admin_api, doctor, clinic, specialization):
    other_user = User.objects.create_user(phone="+998901110077", full_name="Dr. Boshqa")
    other = Doctor.objects.create(user=other_user, status="approved")
    for d in (doctor, other):
        DoctorAffiliation.objects.create(doctor=d, clinic=clinic)
    room = admin_api.post("/api/v1/clinic/rooms", {"name": "101", "equipment": ["UZI"]}, format="json").json()
    assert admin_api.post("/api/v1/clinic/rooms", {"name": "101"}, format="json").status_code == 409

    day = _tomorrow()
    r1, r2 = _rule(doctor, clinic, day), _rule(other, clinic, day)
    generate_slots(doctor.id)
    generate_slots(other.id)

    first = admin_api.post(f"/api/v1/clinic/rules/{r1.id}/room", {"room_id": room["id"]}, format="json").json()
    assert first["moved"] == 4
    second = admin_api.post(f"/api/v1/clinic/rules/{r2.id}/room", {"room_id": room["id"]}, format="json").json()
    assert second == {"moved": 0, "blocked": 4, "conflicts": []}
    assert TimeSlot.objects.filter(room_id=room["id"]).count() == 4

    # Yangi generatsiya ham xonani ikki marta bermaydi
    TimeSlot.objects.filter(doctor=other).delete()
    generate_slots(other.id)
    assert not TimeSlot.objects.filter(doctor=other).exists()


@pytest.mark.django_db
def test_shifokor_klinikadan_chiqadi(doctor, clinic):
    aff = DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    r = api_for(doctor.user).post(f"/api/v1/doctor/affiliations/{aff.id}/leave")
    assert r.json()["status"] == "ended"
    assert not DoctorAffiliation.objects.get(id=aff.id).is_active
    assert Room.objects.count() == 0
