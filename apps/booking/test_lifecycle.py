"""Sprint 1.1 — bron siklini yopish (C1): completed / no_show."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking.services import BookingError, complete_booking, mark_no_show
from apps.booking.models import Booking
from apps.schedule.models import TimeSlot
from apps.utils.models import OutboxEvent


def confirm_in_past(booking, *, started_hours_ago=1, duration_minutes=30):
    start = timezone.now() - timedelta(hours=started_hours_ago)
    TimeSlot.objects.filter(id=booking.slot_id).update(
        status=TimeSlot.Status.BOOKED, hold_expires_at=None,
        start_at=start, end_at=start + timedelta(minutes=duration_minutes),
    )
    Booking.objects.filter(id=booking.id).update(status=Booking.Status.CONFIRMED)


@pytest.fixture
def admin_api(db):
    from apps.account.models import User

    admin = User.objects.create_user(phone="+998900000001", role=User.Role.PLATFORM_ADMIN)
    api = APIClient()
    api.force_authenticate(admin)
    return api


@pytest.mark.django_db
def test_boshlangan_qabul_yakunlanadi_va_hodisa_chiqadi(pending_booking):
    confirm_in_past(pending_booking)
    booking = complete_booking(pending_booking.id, by="doctor", note="Tavsiya: dam olish")

    assert booking.status == Booking.Status.COMPLETED
    assert booking.completed_by == "doctor"
    assert booking.completed_at is not None
    ev = OutboxEvent.objects.get()
    assert ev.event_type == "BookingCompleted"
    assert ev.payload["status"] == "completed"


@pytest.mark.django_db
def test_yakunlash_idempotent(pending_booking):
    confirm_in_past(pending_booking)
    complete_booking(pending_booking.id, by="doctor")
    complete_booking(pending_booking.id, by="system")
    assert OutboxEvent.objects.count() == 1
    pending_booking.refresh_from_db()
    assert pending_booking.completed_by == "doctor"


@pytest.mark.django_db
def test_boshlanmagan_qabulni_yakunlab_bolmaydi(pending_booking):
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.CONFIRMED)
    with pytest.raises(BookingError, match="boshlanmagan"):
        complete_booking(pending_booking.id, by="doctor")


@pytest.mark.django_db
def test_tolanmagan_bronni_yakunlab_bolmaydi(pending_booking):
    with pytest.raises(BookingError):
        complete_booking(pending_booking.id, by="doctor")


@pytest.mark.django_db
def test_no_show_kim_kelmaganini_saqlaydi(pending_booking):
    confirm_in_past(pending_booking)
    booking = mark_no_show(pending_booking.id, absent="doctor", reported_by="admin")
    assert booking.status == Booking.Status.NO_SHOW
    assert booking.no_show_by == "doctor"
    assert OutboxEvent.objects.get().event_type == "BookingNoShow"


@pytest.mark.django_db
def test_yakunlangan_bronni_bekor_qilib_bolmaydi(pending_booking):
    from api.booking.services import cancel_booking

    confirm_in_past(pending_booking)
    complete_booking(pending_booking.id, by="doctor")
    assert cancel_booking(pending_booking.id).status == Booking.Status.COMPLETED


@pytest.mark.django_db
def test_auto_complete_24_soatdan_keyin(pending_booking):
    confirm_in_past(pending_booking, started_hours_ago=26)
    call_command("auto_complete_bookings")
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.COMPLETED
    assert pending_booking.completed_by == "system"


@pytest.mark.django_db
def test_auto_complete_yangi_tugagan_qabulga_tegmaydi(pending_booking):
    confirm_in_past(pending_booking, started_hours_ago=2)
    call_command("auto_complete_bookings")
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CONFIRMED


@pytest.mark.django_db
def test_admin_endpointlari(pending_booking, admin_api):
    confirm_in_past(pending_booking)
    resp = admin_api.post(f"/api/v1/booking/bookings/{pending_booking.id}/no-show", {"absent": "client"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_show"


@pytest.mark.django_db
def test_mijoz_complete_endpointini_chaqira_olmaydi(pending_booking):
    confirm_in_past(pending_booking)
    api = APIClient()
    api.force_authenticate(pending_booking.client)
    resp = api.post(f"/api/v1/booking/bookings/{pending_booking.id}/complete", {}, format="json")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_analitika_yangi_holatlarni_qabul_qiladi():
    from analytics.consumer import _to_fact_row

    data = {"booking_id": "b", "created_date": "2026-09-15", "doctor_id": "d"}
    assert _to_fact_row({"event_type": "BookingCompleted", "data": data})["status"] == "completed"
    assert _to_fact_row({"event_type": "BookingNoShow", "data": data})["status"] == "no_show"
