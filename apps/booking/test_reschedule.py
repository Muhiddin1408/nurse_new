"""C3 — ko'chirish (reschedule).

Ko'chirish bekor qilishning bir qismini saqlab qoladi: mijozning rejasi
o'zgarganda u pul qaytishini kutmasdan boshqa vaqtga o'tadi.

Asosiy invariantlar:
    * eski slot bo'shaydi, yangisi band bo'ladi — ORALIQDA ikkalasi ham emas
    * narx o'zgarmaydi (snapshot tegilmaydi)
    * begona/mos kelmaydigan slotga ko'chirib bo'lmaydi
    * oxirgi 24 soatda va 2 martadan ko'p ko'chirib bo'lmaydi
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking.services import (
    BookingError,
    InvalidBookingRequest,
    reschedule_booking,
)
from apps.booking.models import Booking
from apps.schedule.models import TimeSlot
from apps.utils.models import OutboxEvent


def make_slot(doctor, clinic, *, hours_ahead=72, minutes=30):
    start = (timezone.now() + timedelta(hours=hours_ahead)).replace(second=0, microsecond=0)
    return TimeSlot.objects.create(
        doctor=doctor,
        clinic=clinic,
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        status=TimeSlot.Status.FREE,
    )


def confirm(booking):
    TimeSlot.objects.filter(id=booking.slot_id).update(
        status=TimeSlot.Status.BOOKED, hold_expires_at=None
    )
    Booking.objects.filter(id=booking.id).update(status=Booking.Status.CONFIRMED)
    booking.refresh_from_db()


@pytest.fixture
def far_booking(pending_booking):
    """Ko'chirish oynasi ichidagi bron: qabulga 72 soat bor."""
    start = (timezone.now() + timedelta(hours=72)).replace(second=0, microsecond=0)
    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        start_at=start, end_at=start + timedelta(minutes=30)
    )
    return pending_booking


# ---------------------------------------------------------------------------
# Asosiy oqim
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_kochirishda_eski_slot_boshaydi_yangisi_band_boladi(far_booking, doctor, clinic):
    new_slot = make_slot(doctor, clinic, hours_ahead=96)
    old_slot_id = far_booking.slot_id

    booking = reschedule_booking(far_booking.id, new_slot.id)

    assert booking.slot_id == new_slot.id
    assert booking.rescheduled_count == 1
    assert TimeSlot.objects.get(id=old_slot_id).status == TimeSlot.Status.FREE
    assert TimeSlot.objects.get(id=new_slot.id).status == TimeSlot.Status.HELD


@pytest.mark.django_db
def test_tolangan_bron_kochirilsa_yangi_slot_darhol_booked(far_booking, doctor, clinic):
    """Hold qoldirilsa, to'langan vaqt 10 daqiqada bo'shab ketardi."""
    confirm(far_booking)
    new_slot = make_slot(doctor, clinic, hours_ahead=96)

    reschedule_booking(far_booking.id, new_slot.id)

    assert TimeSlot.objects.get(id=new_slot.id).status == TimeSlot.Status.BOOKED


@pytest.mark.django_db
def test_narx_kochirishda_ozgarmaydi(far_booking, doctor, clinic, service):
    """Shifokor narxini oshirsa ham, ko'chirilgan bron eski narxda qoladi —
    aks holda ko'chirish yashirin narx oshirish vositasiga aylanardi."""
    old_total = far_booking.total_price
    new_slot = make_slot(doctor, clinic, hours_ahead=96)

    service.price = old_total * 3
    service.save(update_fields=["price"])
    booking = reschedule_booking(far_booking.id, new_slot.id)

    assert booking.total_price == old_total
    assert booking.items.first().price == old_total


@pytest.mark.django_db
def test_kochirish_hodisasi_yangi_vaqt_bilan_chiqadi(far_booking, doctor, clinic):
    """Hodisasiz mijoz eski vaqtdagi eslatmani olardi."""
    new_slot = make_slot(doctor, clinic, hours_ahead=96)
    reschedule_booking(far_booking.id, new_slot.id)

    event = OutboxEvent.objects.filter(event_type="BookingRescheduled").get()
    assert event.payload["start_at"].startswith(new_slot.start_at.isoformat()[:16])


# ---------------------------------------------------------------------------
# Cheklovlar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_boshqa_shifokorning_slotiga_kochirib_bolmaydi(far_booking, clinic, specialization):
    """A2 qoidasi: "topilmadi" va "begona" uchun xato bir xil."""
    from apps.account.models import User
    from apps.catalog.models import Doctor

    other_user = User.objects.create_user(phone="+998901110099", role=User.Role.DOCTOR)
    other = Doctor.objects.create(user=other_user, status=Doctor.Status.APPROVED)
    other.specializations.add(specialization)
    foreign_slot = make_slot(other, clinic, hours_ahead=96)

    with pytest.raises(InvalidBookingRequest):
        reschedule_booking(far_booking.id, foreign_slot.id)

    far_booking.refresh_from_db()
    assert far_booking.rescheduled_count == 0


@pytest.mark.django_db
def test_uy_xizmatini_klinika_slotiga_kochirib_bolmaydi(far_booking, doctor):
    """Uy chaqiruvining yo'l buferi va manzil mantiqi butunlay boshqacha."""
    home_slot = make_slot(doctor, None, hours_ahead=96)  # clinic=None -> uy

    with pytest.raises(InvalidBookingRequest):
        reschedule_booking(far_booking.id, home_slot.id)


@pytest.mark.django_db
def test_qisqa_slotga_kochirib_bolmaydi(far_booking, doctor, clinic):
    """60 daqiqalik xizmat 15 daqiqalik slotga sig'maydi (C5 qoidasi)."""
    short_slot = make_slot(doctor, clinic, hours_ahead=96, minutes=15)
    far_booking.items.update(duration_minutes=60)

    with pytest.raises(InvalidBookingRequest):
        reschedule_booking(far_booking.id, short_slot.id)


@pytest.mark.django_db
def test_oxirgi_24_soatda_kochirib_bolmaydi(pending_booking, doctor, clinic):
    """C2 bilan BIR XIL oyna. Aks holda mijoz "bekor qilish pulli, ko'chirish
    bepul" teshigidan foydalanib oxirgi daqiqada jarimasiz chiqib ketardi."""
    start = timezone.now() + timedelta(hours=3)
    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        start_at=start, end_at=start + timedelta(minutes=30)
    )
    new_slot = make_slot(doctor, clinic, hours_ahead=96)

    with pytest.raises(BookingError, match="kam vaqt"):
        reschedule_booking(pending_booking.id, new_slot.id)


@pytest.mark.django_db
def test_ikki_martadan_kop_kochirib_bolmaydi(far_booking, doctor, clinic):
    """Cheklovsiz ko'chirish — slot spekulyatsiyasi: bitta bron bilan bir
    necha vaqtni navbatma-navbat egallab turish."""
    for i in range(2):
        reschedule_booking(far_booking.id, make_slot(doctor, clinic, hours_ahead=96 + i * 2).id)

    with pytest.raises(BookingError, match="ko'pi bilan"):
        reschedule_booking(far_booking.id, make_slot(doctor, clinic, hours_ahead=120).id)


@pytest.mark.django_db
def test_band_slotga_kochirilsa_eski_vaqt_saqlanadi(far_booking, doctor, clinic):
    """TARTIB SINOVI: avval yangi slot band qilinadi, keyin eskisi bo'shaydi.

    Teskari tartibda mijoz ikkala vaqtdan ham ayrilardi."""
    taken = make_slot(doctor, clinic, hours_ahead=96)
    TimeSlot.objects.filter(id=taken.id).update(status=TimeSlot.Status.BOOKED)
    old_slot_id = far_booking.slot_id

    with pytest.raises(BookingError, match="band"):
        reschedule_booking(far_booking.id, taken.id)

    far_booking.refresh_from_db()
    assert far_booking.slot_id == old_slot_id
    assert TimeSlot.objects.get(id=old_slot_id).status == TimeSlot.Status.HELD


@pytest.mark.django_db
def test_yakunlangan_bronni_kochirib_bolmaydi(far_booking, doctor, clinic):
    Booking.objects.filter(id=far_booking.id).update(status=Booking.Status.COMPLETED)
    new_slot = make_slot(doctor, clinic, hours_ahead=96)

    with pytest.raises(BookingError):
        reschedule_booking(far_booking.id, new_slot.id)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoint_kochiradi(far_booking, doctor, clinic, client_user):
    new_slot = make_slot(doctor, clinic, hours_ahead=96)
    api = APIClient()
    api.force_authenticate(client_user)

    resp = api.post(
        f"/api/v1/booking/bookings/{far_booking.id}/reschedule",
        {"new_slot_id": str(new_slot.id)},
        format="json",
    )

    assert resp.status_code == 200
    far_booking.refresh_from_db()
    assert far_booking.slot_id == new_slot.id


@pytest.mark.django_db
def test_begona_bron_404(far_booking, doctor, clinic):
    """A4: begona bron — xuddi mavjud emasdek, 403 emas."""
    from apps.account.models import User

    stranger = User.objects.create_user(phone="+998909998877")
    api = APIClient()
    api.force_authenticate(stranger)

    resp = api.post(
        f"/api/v1/booking/bookings/{far_booking.id}/reschedule",
        {"new_slot_id": str(make_slot(doctor, clinic, hours_ahead=96).id)},
        format="json",
    )

    assert resp.status_code == 404
