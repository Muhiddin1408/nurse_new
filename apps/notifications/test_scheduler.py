"""C9: rejalashtirilgan eslatmalar — T-24, T-2, T+2.

Asosiy sinovlar:
    * tasdiqlangan bron uchun uchta qator yaratiladi
    * takroriy hodisa ikkinchi qatorni yaratmaydi (Kafka takrorlashi)
    * vaqti o'tib ketgan eslatma umuman yaratilmaydi
    * bekor qilingan bronga eslatma KETMAYDI — ikkala himoya qatlami ham
"""

from datetime import timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.booking.models import Booking
from apps.notifications.models import ScheduledNotification
from api.notifications import scheduler

Kind = ScheduledNotification.Kind
Status = ScheduledNotification.Status


def _schedule(booking, *, hours_ahead=48):
    start_at = timezone.now() + timedelta(hours=hours_ahead)
    return scheduler.schedule_booking_reminders(
        booking_id=booking.id,
        start_at=start_at,
        phone="+998901234567",
        number=booking.number,
        place="clinic",
    )


@pytest.fixture
def sms(monkeypatch):
    """SMS yuborishni ushlab qolamiz — testda provayderga chiqmaymiz."""
    sent = []
    monkeypatch.setattr(
        scheduler.notification_services,
        "send_sms",
        lambda **kw: sent.append(kw),
    )
    return sent


# ---------------------------------------------------------------------------
# Rejalashtirish
# ---------------------------------------------------------------------------


def test_tasdiqlangan_bronga_uchta_eslatma_rejalashtiriladi(pending_booking):
    assert _schedule(pending_booking) == 3

    kinds = set(
        ScheduledNotification.objects.filter(booking=pending_booking).values_list("kind", flat=True)
    )
    assert kinds == {Kind.REMINDER_24H, Kind.REMINDER_2H, Kind.REVIEW_REQUEST}


def test_takroriy_hodisa_ikkinchi_qatorni_yaratmaydi(pending_booking):
    """Kafka "kamida bir marta" kafolati — bir xil hodisa ikki marta keladi.

    Ikkinchi chaqiruvdan keyin ham qatorlar soni 3 bo'lishi shart, aks holda
    mijoz har bir eslatmani ikki marta olardi."""
    _schedule(pending_booking)
    _schedule(pending_booking)

    assert ScheduledNotification.objects.filter(booking=pending_booking).count() == 3


def test_vaqti_otib_ketgan_eslatma_yaratilmaydi(pending_booking):
    """Qabulga 3 soat qolganda bron qilindi — T-24 eslatmasi ma'nosiz.

    Yaratilsa, scheduler uni DARHOL yuborar va mijoz qabulga 3 soat qolganda
    "ertaga qabulingiz bor" SMS'ini olardi."""
    _schedule(pending_booking, hours_ahead=3)

    kinds = set(
        ScheduledNotification.objects.filter(booking=pending_booking).values_list("kind", flat=True)
    )
    assert Kind.REMINDER_24H not in kinds
    assert Kind.REMINDER_2H in kinds


# ---------------------------------------------------------------------------
# Yuborish
# ---------------------------------------------------------------------------


def test_vaqti_kelmagan_eslatma_yuborilmaydi(pending_booking, sms):
    _schedule(pending_booking)

    assert scheduler.run_due_notifications() == 0
    assert sms == []


def test_vaqti_kelgan_eslatma_yuboriladi(pending_booking, sms):
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.CONFIRMED)
    _schedule(pending_booking)
    _make_due(pending_booking, Kind.REMINDER_24H)

    assert scheduler.run_due_notifications() == 1
    assert sms[0]["template"] == "booking_reminder_24h"

    row = ScheduledNotification.objects.get(booking=pending_booking, kind=Kind.REMINDER_24H)
    assert row.status == Status.SENT
    assert row.sent_at is not None


def test_yuborilgan_eslatma_ikkinchi_marta_yuborilmaydi(pending_booking, sms):
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.CONFIRMED)
    _schedule(pending_booking)
    _make_due(pending_booking, Kind.REMINDER_24H)

    scheduler.run_due_notifications()
    assert scheduler.run_due_notifications() == 0
    assert len(sms) == 1


# ---------------------------------------------------------------------------
# Bekor qilingan bron — ikki himoya qatlami
# ---------------------------------------------------------------------------


def test_bekor_qilinganda_kutilayotgan_eslatmalar_toxtatiladi(pending_booking):
    """Birinchi qatlam: `BookingCancelled` hodisasi qatorlarni yopadi."""
    _schedule(pending_booking)

    assert scheduler.cancel_booking_reminders(pending_booking.id) == 3
    assert not ScheduledNotification.objects.filter(
        booking=pending_booking, status=Status.PENDING
    ).exists()


def test_bekor_qilingan_bronga_eslatma_ketmaydi_hodisa_kelmasa_ham(pending_booking, sms):
    """Ikkinchi qatlam — bu faylning eng muhim testi.

    Bron bekor qilingan, lekin `cancel_booking_reminders` chaqirilmagan
    (consumer to'xtagan, hodisa kechikkan yoki holat admin paneldan qo'lda
    o'zgartirilgan). Yuborish vaqtidagi holat tekshiruvi SMS'ni to'xtatishi
    shart: mijoz bekor qilgan bron uchun "ertaga qabulingiz bor" olmasin.
    """
    _schedule(pending_booking)
    _make_due(pending_booking, Kind.REMINDER_24H)
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.CANCELLED)

    assert scheduler.run_due_notifications() == 0
    assert sms == []

    row = ScheduledNotification.objects.get(booking=pending_booking, kind=Kind.REMINDER_24H)
    assert row.status == Status.SKIPPED
    assert "cancelled" in row.last_error


def test_baholash_sorovi_kelmagan_mijozga_ketmaydi(pending_booking, sms):
    """`no_show` ga sharh so'rovi yuborilsa — kelmagan mijozdan sharh so'ragan bo'lardik."""
    _schedule(pending_booking)
    _make_due(pending_booking, Kind.REVIEW_REQUEST)
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.NO_SHOW)

    assert scheduler.run_due_notifications() == 0
    assert sms == []
    row = ScheduledNotification.objects.get(booking=pending_booking, kind=Kind.REVIEW_REQUEST)
    assert row.status == Status.SKIPPED


def test_shifokor_yakunlamaguncha_baholash_sorovi_kutadi(pending_booking, sms):
    """Shifokor "yakunlandi" ni kech bosadi — sharh so'rovi TASHLANMAYDI, kutadi.

    Kutmasa, sharh so'rovi bronlarning ko'pchiligida hech qachon ketmasdi
    (shifokor odatda qabuldan 2 soat ichida belgilamaydi) va reyting
    ma'lumotsiz qolardi.
    """
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.CONFIRMED)
    _schedule(pending_booking)
    _make_due(pending_booking, Kind.REVIEW_REQUEST)

    assert scheduler.run_due_notifications() == 0
    row = ScheduledNotification.objects.get(booking=pending_booking, kind=Kind.REVIEW_REQUEST)
    assert row.status == Status.PENDING  # tashlanmadi
    assert row.send_at > timezone.now()  # keyinga surildi

    # Shifokor (yoki `auto_complete_bookings`) yakunladi — endi ketadi
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.COMPLETED)
    _make_due(pending_booking, Kind.REVIEW_REQUEST)
    assert scheduler.run_due_notifications() == 1
    assert sms[0]["template"] == "booking_review_request"


def _make_due(booking, kind):
    """Qatorning vaqtini o'tgan qilib qo'yadi — kutmasdan yuborishni sinash uchun."""
    ScheduledNotification.objects.filter(booking=booking, kind=kind).update(
        send_at=timezone.now() - timedelta(minutes=1)
    )
    ScheduledNotification.objects.filter(booking=booking).exclude(kind=kind).update(
        send_at=timezone.now() + timedelta(days=3)
    )
