"""
booking/rebook.py — "Yana bron qilish" (C15.7).

    GET /api/v1/booking/bookings/{id}/rebook

Eski bron asosida TAYYOR forma: o'sha shifokor, bemor, manzil, xizmatlar va
eng yaqin bo'sh vaqtlar. Bron YARATILMAYDI — ilova natijani bron oynasiga
qo'yadi, mijoz vaqtni tanlab odatdagi `POST /bookings` ni chaqiradi.
Shuning uchun barcha tekshiruvlar (narx, yosh, radius, limitlar) bitta joyda
qoladi — bu yerda takrorlanmaydi.

Narx — HOZIRGI narx, snapshot emas: yangi bron yangi narxda (C3 dagi
ko'chirishdan farqi shu).
"""

from __future__ import annotations

from uuid import UUID

from api.catalog import services as catalog_services
from api.patients import services as patient_services
from api.schedule import services as schedule_services
from apps.booking.models import Booking

SLOTS_LIMIT = 5


class RebookNotFound(Exception):
    pass


def rebook_draft(*, client_id: UUID, booking_id: UUID) -> dict:
    booking = (
        Booking.objects.filter(id=booking_id, client_id=client_id)
        .select_related("slot")
        .prefetch_related("items")
        .first()
    )
    if booking is None:
        raise RebookNotFound

    old_ids = [item.service_id for item in booking.items.all()]
    bookable = {s.id: s for s in catalog_services.get_bookable_services(booking.doctor_id, old_ids)}
    services = [
        {
            "id": str(item.service_id),
            "name": item.service_name,
            "available": item.service_id in bookable,
            "price": str(bookable[item.service_id].price) if item.service_id in bookable else None,
            "previous_price": str(item.price),
        }
        for item in booking.items.all()
    ]

    is_home = booking.slot.clinic_id is None
    patient = patient_services.get_owned_patient(client_id, booking.patient_id)
    address = (
        patient_services.get_owned_address(client_id, booking.address_id) if booking.address_id else None
    )

    doctor_bookable = catalog_services.is_doctor_bookable(booking.doctor_id)
    slots = []
    if doctor_bookable and any(s["available"] for s in services):
        qs = schedule_services.get_available_slots(doctor_id=booking.doctor_id)
        # O'sha joy: uy -> uy sloti; klinika -> o'sha klinika (xizmat klinikaga bog'langan)
        qs = qs.filter(clinic__isnull=True) if is_home else qs.filter(clinic_id=booking.slot.clinic_id)
        needed = sum(bookable[i].duration_minutes for i in old_ids if i in bookable)
        slots = [
            {"slot_id": str(s.id), "start_at": s.start_at.isoformat()}
            for s in qs[: SLOTS_LIMIT * 4]
            if (s.end_at - s.start_at).total_seconds() >= needed * 60
        ][:SLOTS_LIMIT]

    return {
        "doctor_id": str(booking.doctor_id),
        "doctor_bookable": doctor_bookable,
        "place": "home" if is_home else "clinic",
        "clinic_id": str(booking.slot.clinic_id) if booking.slot.clinic_id else None,
        # O'chirilgan bemor/manzil qaytarilmaydi — ilova qayta tanlatadi
        "patient_id": str(patient.id) if patient else None,
        "address_id": str(address.id) if address else None,
        "service_ids": [s["id"] for s in services if s["available"]],
        "services": services,
        "slots": slots,
    }
