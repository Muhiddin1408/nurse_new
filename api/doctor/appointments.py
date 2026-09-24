"""
doctor/appointments.py — shifokorning kunlik ish quroli (D4).

MAXFIYLIK CHEGARASI:
    Shifokor bemorning faqat SHU BRON doirasidagi ma'lumotini ko'radi.
    Telefon raqami va aniq manzil (podyezd, qavat, xonadon) — faqat QABUL
    KUNI. Oldindan emas: marketplace'ning asosiy riski — "shifokor bilan
    platformadan tashqarida kelishib olish". Bemor tarixi — faqat SHU
    shifokor bilan bo'lgan qabullar.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from api.booking import services as booking_services
from api.schedule.services import LOCAL_TZ
from apps.booking.models import Booking
from apps.catalog.models import Doctor

MAX_RANGE_DAYS = 31
VISIBLE_STATUSES = (
    Booking.Status.CONFIRMED, Booking.Status.COMPLETED, Booking.Status.NO_SHOW, Booking.Status.CANCELLED,
)


class AppointmentError(Exception):
    pass


def _base_qs(doctor: Doctor):
    return (Booking.objects.filter(doctor=doctor, status__in=VISIBLE_STATUSES)
            .select_related("slot", "slot__clinic", "patient", "address", "client")
            .prefetch_related("items"))


def get_owned(doctor: Doctor, booking_id: UUID) -> Booking:
    """A4 loader: begona bron — topilmadi (404)."""
    b = _base_qs(doctor).filter(id=booking_id).first()
    if b is None:
        raise AppointmentError("not_found")
    return b


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=LOCAL_TZ)
    return start, start + timedelta(days=1)


def today(doctor: Doctor) -> list[Booking]:
    start, end = _day_bounds(timezone.localtime(timezone.now(), LOCAL_TZ).date())
    return list(_base_qs(doctor).filter(slot__start_at__gte=start, slot__start_at__lt=end)
                .exclude(status=Booking.Status.CANCELLED).order_by("slot__start_at"))


def list_range(doctor: Doctor, date_from: date, date_to: date, status: str | None) -> list[Booking]:
    if date_to < date_from:
        raise AppointmentError("date_to date_from dan oldin")
    if (date_to - date_from).days >= MAX_RANGE_DAYS:
        raise AppointmentError(f"Oraliq {MAX_RANGE_DAYS} kundan oshmasligi kerak")
    start, _ = _day_bounds(date_from)
    _, end = _day_bounds(date_to)
    qs = _base_qs(doctor).filter(slot__start_at__gte=start, slot__start_at__lt=end)
    if status:
        qs = qs.filter(status=status)
    return list(qs.order_by("slot__start_at"))


def patient_history(doctor: Doctor, booking: Booking) -> list[Booking]:
    return list(
        Booking.objects.filter(doctor=doctor, patient_id=booking.patient_id,
                               status__in=[Booking.Status.COMPLETED, Booking.Status.NO_SHOW])
        .exclude(id=booking.id).select_related("slot").prefetch_related("items")
        .order_by("-slot__start_at")[:20]
    )


def is_appointment_day(booking: Booking) -> bool:
    local_today = timezone.localtime(timezone.now(), LOCAL_TZ).date()
    return booking.slot.start_at.astimezone(LOCAL_TZ).date() == local_today


def _age(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def serialize(booking: Booking, *, detail: bool = False, history: list[Booking] | None = None) -> dict:
    slot = booking.slot
    on_day = is_appointment_day(booking)
    patient = booking.patient
    place = "home" if slot.clinic_id is None else "clinic"
    data = {
        "id": str(booking.id),
        "number": booking.number,
        "status": booking.status,
        "start_at": slot.start_at.isoformat(),
        "end_at": slot.end_at.isoformat(),
        "place": place,
        "clinic": {"id": str(slot.clinic_id), "name": slot.clinic.name} if slot.clinic_id else None,
        "patient": {
            "full_name": patient.full_name,
            "age": _age(patient.birth_date, slot.start_at.astimezone(LOCAL_TZ).date()),
            "gender": patient.gender,
            "weight_kg": patient.weight_kg,
        },
        "services": [{"name": i.service_name, "duration_minutes": i.duration_minutes} for i in booking.items.all()],
        "client_comment": booking.client_comment,
        # ⬇ faqat qabul kuni (maxfiylik chegarasi)
        "client_phone": booking.client.phone if on_day else None,
        "contact_available_from": None if on_day else slot.start_at.astimezone(LOCAL_TZ).date().isoformat(),
        "started_at": booking.started_at.isoformat() if booking.started_at else None,
        "completed_at": booking.completed_at.isoformat() if booking.completed_at else None,
        "note": booking.note,
    }
    if place == "home" and booking.address_id:
        a = booking.address
        data["address"] = {
            "city": a.city, "street": a.street, "latitude": str(a.latitude), "longitude": str(a.longitude),
            **({"entrance": a.entrance, "floor": a.floor, "apartment": a.apartment, "comment": a.comment}
               if on_day else {}),
        }
    if detail:
        data["patient_history"] = [
            {"date": h.slot.start_at.astimezone(LOCAL_TZ).date().isoformat(), "status": h.status,
             "services": [i.service_name for i in h.items.all()], "note": h.note}
            for h in (history or [])
        ]
    return data


# --- amallar ----------------------------------------------------------------


def start(doctor: Doctor, booking_id: UUID) -> Booking:
    b = get_owned(doctor, booking_id)
    if b.status != Booking.Status.CONFIRMED:
        raise AppointmentError("Faqat tasdiqlangan qabulni boshlash mumkin")
    # 15 daqiqa oldin boshlashga ruxsat — bemor erta kelishi mumkin
    if b.slot.start_at - timedelta(minutes=15) > timezone.now():
        raise AppointmentError("Qabul vaqti hali kelmagan")
    if b.started_at is None:
        Booking.objects.filter(id=b.id, started_at__isnull=True).update(started_at=timezone.now())
    b.refresh_from_db()
    return b


def complete(doctor: Doctor, booking_id: UUID, note: str) -> Booking:
    get_owned(doctor, booking_id)
    return booking_services.complete_booking(booking_id, by=Booking.Actor.DOCTOR, note=note)


def no_show(doctor: Doctor, booking_id: UUID, note: str) -> Booking:
    get_owned(doctor, booking_id)
    # Shifokor faqat MIJOZ kelmaganini belgilaydi; shifokor kelmagani — mijoz nizosi (B11)
    return booking_services.mark_no_show(booking_id, absent=Booking.Actor.CLIENT,
                                         reported_by=Booking.Actor.DOCTOR, note=note)


def cancel(doctor: Doctor, booking_id: UUID, reason: str) -> Booking:
    get_owned(doctor, booking_id)
    if not reason.strip():
        raise AppointmentError("Bekor qilish sababi majburiy")
    return booking_services.cancel_booking(booking_id, reason=reason, actor="doctor")
