"""C10 — navbat (waitlist).

Band slot = yo'qolgan talab. Navbat uni ushlab qoladi va slot bo'shaganda
qayta sotadi.

Asosiy invariantlar:
    * band slotga urinish 409 + muqobil vaqtlar + navbat taklifi qaytaradi
    * bo'shagan slot haqida FAQAT BITTA (navbatdagi birinchi) odam xabar oladi
    * oynaga/joyga mos kelmaydigan navbat xabar olmaydi
    * bron qilgan odam navbatdan chiqadi
"""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking import waitlist
from apps.booking.models import WaitlistEntry
from apps.schedule.models import TimeSlot

Status = WaitlistEntry.Status


@pytest.fixture
def api(client_user):
    api = APIClient()
    api.force_authenticate(client_user)
    return api


def join(client, doctor, *, days=(1, 7), place="clinic"):
    today = timezone.localdate()
    return waitlist.join(
        client_id=client.id,
        doctor_id=doctor.id,
        date_from=today + timedelta(days=days[0]),
        date_to=today + timedelta(days=days[1]),
        place=place,
    )


def freed_at(hours_ahead=72):
    return timezone.now() + timedelta(hours=hours_ahead)


# ---------------------------------------------------------------------------
# Navbatga yozilish
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_navbatga_yozilish_va_chiqish(api, client_user, doctor):
    today = timezone.localdate()
    resp = api.post(
        "/api/v1/booking/waitlist",
        {
            "doctor_id": str(doctor.id),
            "date_from": str(today + timedelta(days=1)),
            "date_to": str(today + timedelta(days=5)),
            "place": "clinic",
        },
        format="json",
    )
    assert resp.status_code == 201
    entry_id = resp.data["id"]

    assert api.get("/api/v1/booking/waitlist").data[0]["id"] == entry_id
    assert api.delete(f"/api/v1/booking/waitlist/{entry_id}").status_code == 204
    assert api.get("/api/v1/booking/waitlist").data == []


@pytest.mark.django_db
def test_takroriy_yozilish_ikkinchi_qator_yaratmaydi(client_user, doctor):
    """Aks holda bitta odam bitta bo'shagan slot uchun o'n marta SMS olardi."""
    join(client_user, doctor)
    entry = join(client_user, doctor, days=(2, 9))

    assert WaitlistEntry.objects.filter(client=client_user, doctor=doctor).count() == 1
    assert entry.date_to == timezone.localdate() + timedelta(days=9)  # oyna yangilandi


@pytest.mark.django_db
def test_otgan_sanaga_yozib_bolmaydi(client_user, doctor):
    with pytest.raises(waitlist.WaitlistError):
        join(client_user, doctor, days=(-5, -1))


# ---------------------------------------------------------------------------
# Slot bo'shaganda
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_boshagan_slot_haqida_navbatdagi_birinchi_odam_xabar_oladi(client_user, doctor):
    """O'nta odamga bir vaqtda yozsak, to'qqiztasi band slotni ko'rardi —
    bu eslatma emas, umidsizlantirish."""
    from apps.account.models import User

    second = User.objects.create_user(phone="+998905550001")
    first_entry = join(client_user, doctor)
    join(second, doctor)

    notified = []
    assert waitlist.notify_slot_freed(
        doctor_id=doctor.id, start_at=freed_at(), place="clinic",
        notify=lambda entry, when: notified.append(entry.id),
    )

    assert notified == [first_entry.id]  # aynan bittasi, eng erta yozilgani
    first_entry.refresh_from_db()
    assert first_entry.status == Status.NOTIFIED
    assert first_entry.notified_count == 1


@pytest.mark.django_db
def test_yaqinda_xabar_olgan_odam_qayta_bezovta_qilinmaydi(client_user, doctor):
    join(client_user, doctor)
    notified = []
    notify = lambda entry, when: notified.append(entry.id)  # noqa: E731

    waitlist.notify_slot_freed(doctor_id=doctor.id, start_at=freed_at(), place="clinic", notify=notify)
    waitlist.notify_slot_freed(doctor_id=doctor.id, start_at=freed_at(80), place="clinic", notify=notify)

    assert len(notified) == 1


@pytest.mark.django_db
def test_oynaga_mos_kelmaydigan_navbat_xabar_olmaydi(client_user, doctor):
    """Mijoz kelasi haftani so'ragan — bugun bo'shagan vaqt unga kerak emas."""
    join(client_user, doctor, days=(10, 14))

    assert not waitlist.notify_slot_freed(
        doctor_id=doctor.id, start_at=freed_at(), place="clinic", notify=lambda e, w: None
    )


@pytest.mark.django_db
def test_boshqa_joydagi_slot_xabar_bermaydi(client_user, doctor):
    """Uy chaqiruvini kutayotgan odamga klinika sloti taklif qilinmaydi."""
    join(client_user, doctor, place="home")

    assert not waitlist.notify_slot_freed(
        doctor_id=doctor.id, start_at=freed_at(), place="clinic", notify=lambda e, w: None
    )


@pytest.mark.django_db
def test_juda_yaqin_vaqt_uchun_xabar_yuborilmaydi(client_user, doctor):
    """2 soatdan kam qolganda odam ulgurmaydi — SMS puli behuda."""
    join(client_user, doctor, days=(0, 3))

    assert not waitlist.notify_slot_freed(
        doctor_id=doctor.id,
        start_at=timezone.now() + timedelta(minutes=30),
        place="clinic",
        notify=lambda e, w: None,
    )


@pytest.mark.django_db
def test_sms_yiqilsa_ham_navbat_buzilmaydi(client_user, doctor):
    """Xabar ketmasa ham oqim yiqilmaydi — odam navbatda qoladi."""
    entry = join(client_user, doctor)

    def boom(entry, when):
        raise RuntimeError("SMS provayderi yiqildi")

    assert not waitlist.notify_slot_freed(
        doctor_id=doctor.id, start_at=freed_at(), place="clinic", notify=boom
    )
    entry.refresh_from_db()
    assert entry.status == Status.NOTIFIED  # belgi qo'yilgan, qator yo'qolmagan


# ---------------------------------------------------------------------------
# Bronga aylanishi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_bron_qilgan_odam_navbatdan_chiqadi(client_user, doctor):
    entry = join(client_user, doctor)

    waitlist.mark_converted(client_id=client_user.id, doctor_id=doctor.id)

    entry.refresh_from_db()
    assert entry.status == Status.CONVERTED
    assert not waitlist.notify_slot_freed(
        doctor_id=doctor.id, start_at=freed_at(), place="clinic", notify=lambda e, w: None
    )


# ---------------------------------------------------------------------------
# 409 javobi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_band_slot_409_muqobil_vaqtlar_bilan_qaytadi(api, pending_booking, patient, service, doctor, clinic):
    """"Band" — oqimning tugashi emas: mijozga muqobil vaqtlar va navbat taklifi."""
    from apps.account.models import Patient

    # Boshqa bemor: aks holda C7 ("bu bemorda shu vaqtda boshqa qabul bor")
    # slot bandligidan OLDIN ishlab ketadi va biz sinamoqchi bo'lgan
    # yo'lga umuman yetib bormaymiz
    other_patient = Patient.objects.create(
        owner=patient.owner, full_name="Boshqa bemor", birth_date=patient.birth_date,
        gender=patient.gender,
    )

    start = timezone.now() + timedelta(hours=80)
    TimeSlot.objects.create(
        doctor=doctor, clinic=clinic, start_at=start,
        end_at=start + timedelta(minutes=30), status=TimeSlot.Status.FREE,
    )

    resp = api.post(
        "/api/v1/booking/bookings",
        {
            "patient_id": str(other_patient.id),
            "doctor_id": str(doctor.id),
            "slot_id": str(pending_booking.slot_id),  # allaqachon HELD
            "service_ids": [str(service.id)],
        },
        format="json",
    )

    assert resp.status_code == 409
    assert resp.data["code"] == "slot_taken"
    assert resp.data["waitlist_available"] is True
    assert len(resp.data["alternatives"]) == 1
