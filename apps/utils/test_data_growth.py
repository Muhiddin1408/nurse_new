"""E14 — ma'lumot o'sishi: retention tozalash va o'sish metrikalari."""

import itertools
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.booking.models import Booking, WaitlistEntry
from apps.schedule.models import TimeSlot
from apps.utils.management.commands import purge_expired_data
from apps.utils.management.commands.purge_expired_data import purge


_seq = itertools.count()


def _slot(doctor, clinic, *, days_ago=None, days_ahead=None, status=TimeSlot.Status.FREE):
    delta = timedelta(days=days_ahead if days_ahead is not None else -days_ago)
    # `UNIQUE(doctor, start_at)` — har bir slot o'z daqiqasida
    start = timezone.now() + delta + timedelta(minutes=next(_seq))
    return TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start,
                                   end_at=start + timedelta(minutes=30), status=status)


@pytest.mark.django_db
def test_eski_bosh_slot_ochiriladi_yangisi_qoladi(doctor, clinic):
    old = _slot(doctor, clinic, days_ago=120)
    recent = _slot(doctor, clinic, days_ago=10)
    future = _slot(doctor, clinic, days_ahead=5)

    assert purge()["time_slots"] == 1
    assert not TimeSlot.objects.filter(id=old.id).exists()
    assert TimeSlot.objects.filter(id__in=[recent.id, future.id]).count() == 2


@pytest.mark.django_db
def test_bronli_slot_hech_qachon_ochirilmaydi(doctor, clinic, client_user, patient):
    """`Booking.slot` PROTECT — filtr bo'lmasa job `ProtectedError` bilan yiqilardi.
    Bron tarixi slot vaqtiga tayanadi, shuning uchun u qoladi."""
    booked = _slot(doctor, clinic, days_ago=400, status=TimeSlot.Status.BOOKED)
    Booking.objects.create(number="OLD-1", client=client_user, patient=patient, doctor=doctor,
                           slot=booked, status=Booking.Status.COMPLETED, total_price=1)

    assert purge()["time_slots"] == 0
    assert TimeSlot.objects.filter(id=booked.id).exists()


@pytest.mark.django_db
def test_yopilgan_navbat_tozalanadi_kutayotgani_qoladi(doctor, client_user):
    from apps.account.models import User

    # `uniq_active_waitlist_per_doctor` — bir mijozda bir shifokorga bitta
    # faol navbat, shuning uchun `notified` boshqa mijozniki
    other = User.objects.create_user(phone="+998901110003", full_name="Ikkinchi")

    def entry(status, days_ago, client=None):
        e = WaitlistEntry.objects.create(
            client=client or client_user, doctor=doctor, status=status, place="clinic",
            date_from=timezone.now().date(), date_to=timezone.now().date(),
        )
        WaitlistEntry.objects.filter(id=e.id).update(created_at=timezone.now() - timedelta(days=days_ago))
        return e

    closed = entry(WaitlistEntry.Status.CONVERTED, 200)
    expired = entry(WaitlistEntry.Status.EXPIRED, 200)
    # Hali javob kutayotganlar — muddatidan qat'i nazar qoladi
    active = entry(WaitlistEntry.Status.ACTIVE, 200)
    notified = entry(WaitlistEntry.Status.NOTIFIED, 200, client=other)
    recent_closed = entry(WaitlistEntry.Status.CONVERTED, 5)

    assert purge()["waitlist"] == 2
    assert not WaitlistEntry.objects.filter(id__in=[closed.id, expired.id]).exists()
    assert WaitlistEntry.objects.filter(id__in=[active.id, notified.id, recent_closed.id]).count() == 3


@pytest.mark.django_db
def test_bolaklab_ochirish_hammasini_tozalaydi(doctor, clinic, monkeypatch):
    """CHUNK dan ko'p qator bo'lsa ham hammasi o'chadi — bitta bo'lak emas."""
    monkeypatch.setattr(purge_expired_data, "CHUNK", 2)
    for _ in range(7):
        _slot(doctor, clinic, days_ago=200)

    assert purge()["time_slots"] == 7
    assert not TimeSlot.objects.filter(start_at__lt=timezone.now() - timedelta(days=90)).exists()


@pytest.mark.django_db
def test_bir_yurishda_maksimal_bolak_cheklangan(doctor, clinic, monkeypatch):
    """Backlog juda katta bo'lsa job tugaydi va ertaga davom etadi — cheksiz sikl yo'q."""
    monkeypatch.setattr(purge_expired_data, "CHUNK", 2)
    monkeypatch.setattr(purge_expired_data, "MAX_CHUNKS", 2)
    for _ in range(9):
        _slot(doctor, clinic, days_ago=200)

    assert purge()["time_slots"] == 4          # 2 bo'lak × 2 qator
    assert TimeSlot.objects.count() == 5       # qolgani keyingi yurishga


@pytest.mark.django_db
def test_osish_metrikasi_postgresdan_boshqa_bazada_jim(doctor, clinic):
    """SQLite'da (test) collector hech narsa chiqarmaydi va YIQILMAYDI —
    scrape hech qachon 500 bermasligi kerak."""
    from api.observability.metrics import TableSizeCollector

    assert list(TableSizeCollector().collect()) == []
