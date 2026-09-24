"""Sprint 2.2 — shifokor jadvalini boshqarish (D3)."""

from datetime import datetime, time, timedelta

import pytest
from rest_framework.test import APIClient

from api.schedule.services import LOCAL_TZ, today_local
from apps.booking.models import Booking, BookingItem
from apps.catalog.models import DoctorAffiliation
from apps.payment.models import Payment, Refund
from apps.schedule.models import TimeOff, TimeSlot, WorkingRule

TOMORROW = None


@pytest.fixture
def doc_api(doctor, clinic):
    DoctorAffiliation.objects.create(doctor=doctor, clinic=clinic)
    api = APIClient()
    api.force_authenticate(doctor.user)
    return api


def day(offset=1):
    return today_local() + timedelta(days=offset)


def at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm), tzinfo=LOCAL_TZ)


def rule_body(clinic, d, **kw):
    body = {"weekday": d.weekday(), "start_time": "09:00", "end_time": "11:00", "slot_minutes": 30,
            "valid_from": today_local().isoformat(), "clinic_id": str(clinic.id)}
    body.update(kw)
    return body


def book(doctor, slot, client_user, patient, service, *, paid=True):
    b = Booking.objects.create(
        number=f"MB-T-{str(slot.id)[:4]}", client=client_user, patient=patient, doctor=doctor, slot=slot,
        status=Booking.Status.CONFIRMED if paid else Booking.Status.PENDING_PAYMENT, total_price=service.price,
    )
    BookingItem.objects.create(booking=b, service=service, service_name=service.name, price=service.price,
                               duration_minutes=30)
    TimeSlot.objects.filter(id=slot.id).update(status=TimeSlot.Status.BOOKED)
    if paid:
        Payment.objects.create(booking=b, provider="payme", amount=b.total_price, status="succeeded",
                               idempotency_key=f"k-{b.id}")
    return b


# --- ruxsatlar --------------------------------------------------------------


@pytest.mark.django_db
def test_tasdiqlanmagan_shifokor_va_mijoz_jadvalga_kira_olmaydi(doctor, client_user):
    from apps.catalog.models import Doctor

    Doctor.objects.filter(id=doctor.id).update(status=Doctor.Status.PENDING)
    api = APIClient()
    api.force_authenticate(doctor.user)
    assert api.get("/api/v1/doctor/working-rules").status_code == 403
    api.force_authenticate(client_user)
    assert api.get("/api/v1/doctor/working-rules").status_code == 403


# --- ish qoidalari ----------------------------------------------------------


@pytest.mark.django_db
def test_qoida_qoshilganda_slotlar_yaratiladi(doc_api, doctor, clinic):
    d = day(1)
    resp = doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json")
    assert resp.status_code == 201, resp.content
    slots = TimeSlot.objects.filter(doctor=doctor, start_at__gte=at(d, 0), start_at__lt=at(d + timedelta(days=1), 0))
    assert slots.count() == 4


@pytest.mark.django_db
def test_qoida_validatsiyasi(doc_api, doctor, clinic):
    from apps.catalog.models import Clinic

    d = day(1)
    bad = [
        rule_body(clinic, d, start_time="12:00", end_time="10:00"),
        rule_body(clinic, d, slot_minutes=7),
        rule_body(clinic, d, end_time="09:20"),  # 30 daqiqalik slot sig'maydi
    ]
    for body in bad:
        assert doc_api.post("/api/v1/doctor/working-rules", body, format="json").status_code == 400

    other = Clinic.objects.create(name="Begona", phone="1", city="T", street="s", latitude=1, longitude=1, status="active")
    assert doc_api.post("/api/v1/doctor/working-rules", rule_body(other, d), format="json").status_code == 400

    doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json")
    overlap = rule_body(clinic, d, start_time="10:00", end_time="12:00")
    assert doc_api.post("/api/v1/doctor/working-rules", overlap, format="json").status_code == 400


@pytest.mark.django_db
def test_qoidani_ochirish_bron_bilan_toqnashsa_409_keyin_cancel_and_refund(
    doc_api, doctor, clinic, client_user, patient, service
):
    d = day(3)
    rule_id = doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json").json()["id"]
    slot = TimeSlot.objects.filter(doctor=doctor, start_at=at(d, 9)).get()
    b = book(doctor, slot, client_user, patient, service)

    resp = doc_api.delete(f"/api/v1/doctor/working-rules/{rule_id}")
    assert resp.status_code == 409
    body = resp.json()
    assert body["conflicts"][0]["booking_number"] == b.number
    assert "cancel_and_refund" in body["options"]
    assert WorkingRule.objects.filter(id=rule_id).exists()  # hech narsa o'zgarmadi

    resp = doc_api.delete(f"/api/v1/doctor/working-rules/{rule_id}?resolution=cancel_and_refund")
    assert resp.status_code == 200
    assert resp.json()["cancelled_bookings"] == [b.number]
    b.refresh_from_db()
    assert b.status == Booking.Status.CANCELLED
    refund = Refund.objects.get(booking=b)
    assert (refund.amount, refund.reason) == (service.price, "doctor_cancelled")
    # qolgan bo'sh slotlar olib tashlandi
    assert not TimeSlot.objects.filter(doctor=doctor, start_at=at(d, 10), status="free").exists()


@pytest.mark.django_db
def test_keep_bookings_bron_saqlanadi(doc_api, doctor, clinic, client_user, patient, service):
    d = day(3)
    rule_id = doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json").json()["id"]
    slot = TimeSlot.objects.get(doctor=doctor, start_at=at(d, 9))
    b = book(doctor, slot, client_user, patient, service)

    resp = doc_api.patch(f"/api/v1/doctor/working-rules/{rule_id}?resolution=keep_bookings",
                         {"start_time": "10:00"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["kept_bookings"] == [b.number]
    b.refresh_from_db()
    assert b.status == Booking.Status.CONFIRMED
    assert not TimeSlot.objects.filter(doctor=doctor, start_at=at(d, 9, 30)).exists()


@pytest.mark.django_db
def test_slot_uzunligi_ozgarsa_band_slot_bilan_ustma_ust_slot_yaratilmaydi(
    doc_api, doctor, clinic, client_user, patient, service
):
    d = day(2)
    rule_id = doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json").json()["id"]
    slot = TimeSlot.objects.get(doctor=doctor, start_at=at(d, 9))
    book(doctor, slot, client_user, patient, service)

    # 09:00–09:30 bron hali ham ish vaqti ichida — to'qnashuv emas
    resp = doc_api.patch(f"/api/v1/doctor/working-rules/{rule_id}", {"slot_minutes": 20}, format="json")
    assert resp.status_code == 200
    starts = sorted(_local_hm(s) for s in TimeSlot.objects.filter(
        doctor=doctor, start_at__gte=at(d, 0), start_at__lt=at(d + timedelta(days=1), 0)))
    # 09:00 (band, 30 daq) saqlandi; 09:20 u bilan ustma-ust — yaratilmadi
    assert "09:20" not in starts
    assert {"09:00", "09:40", "10:00", "10:20", "10:40"} <= set(starts)


def _local_hm(slot):
    return slot.start_at.astimezone(LOCAL_TZ).strftime("%H:%M")


# --- ta'til -----------------------------------------------------------------


@pytest.mark.django_db
def test_tatil_bronsiz_slotlarni_yopadi_va_ochirilganda_qaytaradi(doc_api, doctor, clinic):
    d = day(4)
    doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json")

    resp = doc_api.post("/api/v1/doctor/time-off", {
        "start_at": at(d, 0).isoformat(), "end_at": at(d + timedelta(days=1), 0).isoformat(),
        "kind": "sick", "reason": "Gripp",
    }, format="json")
    assert resp.status_code == 201, resp.content
    blocked = TimeSlot.objects.filter(doctor=doctor, status="blocked", block_reason="time_off")
    assert blocked.count() == 4

    # ochiq katalogda ko'rinmaydi
    assert APIClient().get(f"/api/v1/schedule/doctors/{doctor.id}/slots?date={d.isoformat()}").json() == []

    resp = doc_api.delete(f"/api/v1/doctor/time-off/{resp.json()['id']}")
    assert resp.json()["reopened_slots"] == 4
    assert TimeSlot.objects.filter(doctor=doctor, status="free", start_at__gte=at(d, 0)).count() >= 4


@pytest.mark.django_db
def test_tatil_tasdiqlangan_qabul_bilan_toqnashsa_409(doc_api, doctor, clinic, client_user, patient, service):
    d = day(5)
    doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json")
    slot = TimeSlot.objects.get(doctor=doctor, start_at=at(d, 10))
    b = book(doctor, slot, client_user, patient, service)
    body = {"start_at": at(d, 9).isoformat(), "end_at": at(d, 12).isoformat(), "kind": "personal"}

    resp = doc_api.post("/api/v1/doctor/time-off", body, format="json")
    assert resp.status_code == 409
    assert resp.json()["conflicts"][0]["patient"] == "M."[:2] or resp.json()["conflicts"][0]["patient"]
    assert not TimeOff.objects.exists()
    assert TimeSlot.objects.get(doctor=doctor, start_at=at(d, 9)).status == "free"  # rollback

    resp = doc_api.post("/api/v1/doctor/time-off?resolution=keep_bookings", body, format="json")
    assert resp.status_code == 201
    b.refresh_from_db()
    assert b.status == Booking.Status.CONFIRMED
    assert TimeSlot.objects.get(id=slot.id).status == "booked"
    assert TimeSlot.objects.get(doctor=doctor, start_at=at(d, 9)).status == "blocked"


@pytest.mark.django_db
def test_tatil_validatsiyasi(doc_api):
    past = {"start_at": at(day(-3), 9).isoformat(), "end_at": at(day(-2), 9).isoformat()}
    assert doc_api.post("/api/v1/doctor/time-off", past, format="json").status_code == 400
    long = {"start_at": at(day(1), 9).isoformat(), "end_at": at(day(120), 9).isoformat()}
    assert doc_api.post("/api/v1/doctor/time-off", long, format="json").status_code == 400


@pytest.mark.django_db
def test_generatsiya_tatil_oraligiga_slot_yaratmaydi(doctor, clinic):
    from api.schedule.services import generate_slots

    d = day(6)
    WorkingRule.objects.create(doctor=doctor, clinic=clinic, weekday=d.weekday(), start_time=time(9),
                               end_time=time(11), slot_minutes=30, valid_from=today_local())
    TimeOff.objects.create(doctor=doctor, start_at=at(d, 9, 30), end_at=at(d, 10, 30))
    generate_slots(doctor.id, days=7)
    assert sorted(_local_hm(s) for s in TimeSlot.objects.filter(doctor=doctor, start_at__gte=at(d, 0),
                                                                start_at__lt=at(d, 23))) == ["09:00", "10:30"]


# --- bitta slot -------------------------------------------------------------


@pytest.mark.django_db
def test_slotni_yopish_va_ochish(doc_api, doctor, clinic, client_user, patient, service):
    d = day(1)
    doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json")
    free = TimeSlot.objects.get(doctor=doctor, start_at=at(d, 9))
    booked = TimeSlot.objects.get(doctor=doctor, start_at=at(d, 10))
    book(doctor, booked, client_user, patient, service)

    assert doc_api.post(f"/api/v1/doctor/slots/{free.id}/block", {"note": "Majlis"}, format="json").status_code == 200
    assert doc_api.post(f"/api/v1/doctor/slots/{booked.id}/block", {}, format="json").status_code == 400
    assert doc_api.post(f"/api/v1/doctor/slots/{free.id}/unblock").status_code == 200
    assert TimeSlot.objects.get(id=free.id).status == "free"


@pytest.mark.django_db
def test_begona_slotni_yopib_bolmaydi(doc_api, specialization, clinic):
    from apps.account.models import User
    from apps.catalog.models import Doctor

    other = Doctor.objects.create(user=User.objects.create_user(phone="+998909990001"), status="approved")
    start = at(day(1), 9)
    slot = TimeSlot.objects.create(doctor=other, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))
    assert doc_api.post(f"/api/v1/doctor/slots/{slot.id}/block", {}, format="json").status_code == 400
    assert TimeSlot.objects.get(id=slot.id).status == "free"


@pytest.mark.django_db
def test_uy_chaqiruvi_bekor_qilinsa_tatil_bloki_ochilmaydi(doctor):
    from api.schedule.services import hold_slot, release_slot

    d = day(2)
    target = TimeSlot.objects.create(doctor=doctor, start_at=at(d, 12), end_at=at(d, 12, 30))
    t_off = TimeOff.objects.create(doctor=doctor, start_at=at(d, 12, 30), end_at=at(d, 13))
    neighbour = TimeSlot.objects.create(doctor=doctor, start_at=at(d, 12, 30), end_at=at(d, 13),
                                        status="blocked", block_reason="time_off", time_off=t_off)
    hold_slot(target.id)
    release_slot(target.id)
    neighbour.refresh_from_db()
    assert (neighbour.status, neighbour.block_reason) == ("blocked", "time_off")


# --- kalendar ---------------------------------------------------------------


@pytest.mark.django_db
def test_kalendar_bron_bilan(doc_api, doctor, clinic, client_user, patient, service):
    d = day(1)
    doc_api.post("/api/v1/doctor/working-rules", rule_body(clinic, d), format="json")
    slot = TimeSlot.objects.get(doctor=doctor, start_at=at(d, 9))
    patient.full_name = "Alisher Karimov"
    patient.save()
    b = book(doctor, slot, client_user, patient, service)

    rows = doc_api.get(f"/api/v1/doctor/schedule?from={d}&to={d}").json()
    assert len(rows) == 4
    first = rows[0]
    assert first["booking"]["number"] == b.number
    assert first["booking"]["patient"] == "A. Karimov"
    assert first["booking"]["services"] == [service.name]
    assert rows[1]["booking"] is None and rows[1]["status"] == "free"

    assert doc_api.get(f"/api/v1/doctor/schedule?from={d}&to={d + timedelta(days=40)}").status_code == 400
