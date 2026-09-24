"""Davriy ishlar: `generate_slots` va `expire_bookings` management command'lari."""

from datetime import date, time, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone


def _rule(doctor, clinic, **kwargs):
    from apps.schedule.models import WorkingRule

    defaults = dict(
        doctor=doctor,
        clinic=clinic,
        start_time=time(9, 0),
        end_time=time(11, 0),  # 30 daqiqalik 4 ta slot
        slot_minutes=30,
        valid_from=date.today() - timedelta(days=1),
    )
    defaults.update(kwargs)
    return [WorkingRule.objects.create(weekday=wd, **defaults) for wd in range(7)]


@pytest.mark.django_db
def test_generate_slots_barcha_shifokorlar_uchun_slot_yaratadi(doctor, clinic):
    from apps.schedule.models import TimeSlot

    _rule(doctor, clinic)
    call_command("generate_slots", "--days", "6")

    # bugun + 6 kun = 7 kun, har kuni 4 slot
    assert TimeSlot.objects.filter(doctor=doctor).count() == 28


@pytest.mark.django_db
def test_generate_slots_ikki_marta_ishlasa_dublikat_yoq(doctor, clinic):
    from apps.schedule.models import TimeSlot

    _rule(doctor, clinic)
    call_command("generate_slots", "--days", "3")
    call_command("generate_slots", "--days", "3")

    assert TimeSlot.objects.filter(doctor=doctor).count() == 16


@pytest.mark.django_db
def test_muddati_otgan_qoida_hisobga_olinmaydi(doctor, clinic):
    from apps.schedule.models import TimeSlot

    _rule(doctor, clinic, valid_from=date.today() - timedelta(days=30),
          valid_to=date.today() - timedelta(days=1))
    call_command("generate_slots", "--days", "3")

    assert TimeSlot.objects.count() == 0


@pytest.mark.django_db
def test_expire_bookings_bron_expired_va_slot_bosh(pending_booking):
    """Tartib tekshiruvi: slot avval bo'shatilsa, bron PENDING_PAYMENT da qotib qolardi."""
    from apps.booking.models import Booking
    from apps.schedule.models import TimeSlot

    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )

    call_command("expire_bookings")

    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.EXPIRED
    assert TimeSlot.objects.get(id=pending_booking.slot_id).status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_expire_bookings_muddati_otmagan_bronga_tegmaydi(pending_booking):
    from apps.booking.models import Booking

    call_command("expire_bookings")

    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.PENDING_PAYMENT
