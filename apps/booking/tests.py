from django.test import TestCase

from api.schedule.services import hold_slot, SlotNotAvailable, release_expired_holds, confirm_slot, release_slot

# Create your tests here.
"""
schedule/tests/test_concurrency.py — Faza 1

Bu fayldagi testlar sizning ASOSIY INVARIANTINGIZNI isbotlaydi:
"bitta shifokor bitta vaqtda faqat bitta joyda band bo'ladi".

Bu shunchaki test emas — bu loyihaning eng qimmatli hujjati. Intervyuda
"double booking'ni qanday oldini oldingiz?" degan savolga siz gap bilan emas,
shu fayl bilan javob berasiz.

Ishga tushirish:
    pytest schedule/tests/test_concurrency.py -v
"""

import threading
from datetime import timedelta

import pytest
from django.db import IntegrityError, connections
from django.utils import timezone

from apps.schedule.models import TimeSlot


# ---------------------------------------------------------------------------
# Yordamchilar
# ---------------------------------------------------------------------------


def make_slot(doctor, *, offset_minutes=120, duration=30, clinic=None):
    start = timezone.now() + timedelta(minutes=offset_minutes)
    # Soniya va mikrosoniyani tozalaymiz — aks holda testlar beqaror bo'ladi
    start = start.replace(second=0, microsecond=0)
    return TimeSlot.objects.create(
        doctor=doctor,
        clinic=clinic,
        start_at=start,
        end_at=start + timedelta(minutes=duration),
        status=TimeSlot.Status.FREE,
    )


# ---------------------------------------------------------------------------
# 1. Baza darajasidagi kafolat
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bir_shifokorga_bir_vaqtda_ikki_slot_yaratib_bolmaydi(doctor, clinic):
    """UNIQUE constraint ishlayotganini isbotlaydi.

    DIQQAT: ikkinchi slot BOSHQA klinikaga tegishli, lekin baribir yaratilmaydi.
    Aynan shu — shifokor bir vaqtda ikki joyda bo'la olmasligining kafolati.
    Constraint'da `clinic` yo'qligi ataylab qilingan.
    """
    slot = make_slot(doctor, clinic=clinic)

    with pytest.raises(IntegrityError):
        TimeSlot.objects.create(
            doctor=doctor,
            clinic=None,  # uy chaqiruvi — baribir bo'lmaydi
            start_at=slot.start_at,
            end_at=slot.end_at,
        )


@pytest.mark.django_db
def test_tugash_vaqti_boshlanishdan_keyin_bolishi_shart(doctor):
    start = timezone.now() + timedelta(days=1)
    with pytest.raises(IntegrityError):
        TimeSlot.objects.create(
            doctor=doctor,
            start_at=start,
            end_at=start - timedelta(minutes=30),  # teskari
        )


# ---------------------------------------------------------------------------
# 2. Parallel bandlik — asosiy test
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_yuz_parallel_sorovdan_faqat_bittasi_band_qiladi(doctor, clinic):
    """ENG MUHIM TEST.

    100 ta thread bir vaqtda bitta slotni band qilmoqchi bo'ladi.
    Aynan bittasi muvaffaqiyatli bo'lishi kerak.

    `transaction=True` majburiy — oddiy `django_db` har bir testni bitta
    tranzaksiyaga o'raydi va thread'lar bir-birining ma'lumotini ko'rmaydi,
    ya'ni test yolg'on yashil bo'ladi.

    `Barrier` — barcha thread'lar bir vaqtda start olishi uchun. Usiz thread'lar
    ketma-ket ishlab ketadi va race condition umuman yuzaga kelmaydi.
    """
    slot = make_slot(doctor, clinic=clinic)

    THREADS = 100
    barrier = threading.Barrier(THREADS)
    results: list[str | None] = [None] * THREADS

    def worker(index: int):
        try:
            barrier.wait()  # hamma shu yerda kutadi, keyin birga yuguradi
            hold_slot(slot.id)
            results[index] = "ok"
        except SlotNotAvailable:
            results[index] = "band"
        except Exception as exc:  # noqa: BLE001
            results[index] = f"xato: {exc!r}"
        finally:
            # Har bir thread o'z DB ulanishini yopishi shart, aks holda
            # test tugagandan keyin ulanishlar osilib qoladi
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ok_count = results.count("ok")
    busy_count = results.count("band")
    errors = [r for r in results if r and r.startswith("xato")]

    assert errors == [], f"Kutilmagan xatolar: {errors[:3]}"
    assert ok_count == 1, f"Aynan 1 ta muvaffaqiyat kutilgandi, {ok_count} ta chiqdi"
    assert busy_count == THREADS - 1

    slot.refresh_from_db()
    assert slot.status == TimeSlot.Status.HELD
    assert slot.hold_expires_at is not None


# ---------------------------------------------------------------------------
# 3. Hold hayot sikli
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_muddati_otgan_hold_avtomatik_boshaydi(doctor, clinic):
    """Mijoz to'lov oynasini yopib ketsa, slot abadiy band qolmasligi kerak."""
    slot = make_slot(doctor, clinic=clinic)
    hold_slot(slot.id, ttl_minutes=10)

    # Vaqtni "orqaga surib" muddati o'tgan holatni yasaymiz
    TimeSlot.objects.filter(id=slot.id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )

    released = release_expired_holds()

    slot.refresh_from_db()
    assert released == 1
    assert slot.status == TimeSlot.Status.FREE
    assert slot.hold_expires_at is None


@pytest.mark.django_db
def test_hold_dan_keyin_tasdiqlash(doctor, clinic):
    slot = make_slot(doctor, clinic=clinic)
    hold_slot(slot.id)
    confirm_slot(slot.id)

    slot.refresh_from_db()
    assert slot.status == TimeSlot.Status.BOOKED
    assert slot.hold_expires_at is None


@pytest.mark.django_db
def test_boshashtirish_idempotent(doctor, clinic):
    """Saga kompensatsiyasi ikki marta chaqirilsa ham xato bermasligi kerak.

    Faza 4 da bu hayotiy: Kafka xabari takrorlanishi butunlay normal hol.
    """
    slot = make_slot(doctor, clinic=clinic)
    hold_slot(slot.id)

    release_slot(slot.id)
    release_slot(slot.id)  # ikkinchi marta — xato bo'lmasligi kerak

    slot.refresh_from_db()
    assert slot.status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_bosh_bolmagan_slotni_band_qilib_bolmaydi(doctor, clinic):
    slot = make_slot(doctor, clinic=clinic)
    TimeSlot.objects.filter(id=slot.id).update(status=TimeSlot.Status.BLOCKED)

    with pytest.raises(SlotNotAvailable):
        hold_slot(slot.id)


# ---------------------------------------------------------------------------
# 4. Uy chaqiruvi — yo'l vaqti buferi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_uy_chaqiruvi_qoshni_slotlarni_yopadi(doctor):
    """Shifokor 12:00 da bemor uyida bo'lsa, 12:30 da boshqa manzilda bo'lolmaydi."""
    base = 24 * 60  # ertaga
    target = make_slot(doctor, offset_minutes=base, clinic=None)
    neighbour_after = make_slot(doctor, offset_minutes=base + 30, clinic=None)
    neighbour_before = make_slot(doctor, offset_minutes=base - 30, clinic=None)
    far_away = make_slot(doctor, offset_minutes=base + 180, clinic=None)

    hold_slot(target.id)

    for slot, expected in [
        (neighbour_after, TimeSlot.Status.BLOCKED),
        (neighbour_before, TimeSlot.Status.BLOCKED),
        (far_away, TimeSlot.Status.FREE),
    ]:
        slot.refresh_from_db()
        assert slot.status == expected, f"{slot.start_at:%H:%M} kutilgan: {expected}"


@pytest.mark.django_db
def test_uy_chaqiruvi_bekor_qilinsa_qoshnilar_boshaydi(doctor):
    base = 24 * 60
    target = make_slot(doctor, offset_minutes=base, clinic=None)
    neighbour = make_slot(doctor, offset_minutes=base + 30, clinic=None)

    hold_slot(target.id)
    release_slot(target.id)

    neighbour.refresh_from_db()
    assert neighbour.status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_klinikadagi_qabul_bufer_talab_qilmaydi(doctor, clinic):
    """Klinikada shifokor joyidan qimirlamaydi — yo'l vaqti kerak emas."""
    base = 24 * 60
    target = make_slot(doctor, offset_minutes=base, clinic=clinic)
    neighbour = make_slot(doctor, offset_minutes=base + 30, clinic=clinic)

    hold_slot(target.id)

    neighbour.refresh_from_db()
    assert neighbour.status == TimeSlot.Status.FREE