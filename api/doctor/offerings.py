"""
doctor/offerings.py — xizmat va narx boshqaruvi (D5), shifokor va klinika admini uchun.

Qoidalar:
  * Narx o'zgarishi MAVJUD bronlarga ta'sir qilmaydi — `BookingItem` snapshot.
  * Har bir narx o'zgarishi `ServicePriceHistory` ga yoziladi.
  * Keskin oshirish (> 50%) — `confirm_large_change=true` bo'lmasa 409 ogohlantirish.
  * O'chirish: xizmat hech bronda ishlatilmagan bo'lsa haqiqatan o'chiriladi,
    aks holda faqat `is_active=False` (tarix va nizolar uchun saqlanadi).
  * Har bir o'zgarish `catalog.events` ga (E7) — qidiruvdagi narx yangilanadi.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from django.db import transaction
from api import audit
from django.utils import timezone

from api.catalog.events import publish_doctor_event
from apps.booking.models import BookingItem
from apps.catalog.models import Doctor, DoctorAffiliation, Service, ServicePriceHistory

LARGE_INCREASE_RATIO = Decimal("1.5")
MAX_PRICE = Decimal("100000000")


class OfferingError(Exception):
    pass


class LargePriceChange(Exception):
    def __init__(self, old: Decimal, new: Decimal):
        super().__init__(f"Narx {old} dan {new} ga oshirilmoqda (> 50%). Tasdiqlash uchun confirm_large_change=true")
        self.old, self.new = old, new


def _validate(data: dict, *, doctor: Doctor | None, specialization_ids: set) -> None:
    if "price" in data and not (Decimal("0") < data["price"] <= MAX_PRICE):
        raise OfferingError("Narx musbat va chegaradan oshmasligi kerak")
    if "duration_minutes" in data and not 5 <= data["duration_minutes"] <= 480:
        raise OfferingError("Davomiylik 5–480 daqiqa")
    if "specialization_id" in data and specialization_ids and data["specialization_id"] not in specialization_ids:
        raise OfferingError("Bu mutaxassislik profilingizda yo'q")
    if doctor is not None and data.get("place") == Service.Place.HOME and not doctor.accepts_home_visits:
        raise OfferingError("Uy xizmati uchun profilda uy chaqiruvini yoqing")


def _affected_doctor_ids(service: Service) -> list[UUID]:
    if service.doctor_id:
        return [service.doctor_id]
    return list(DoctorAffiliation.objects.filter(clinic_id=service.clinic_id, is_active=True,
                                                 doctor__status=Doctor.Status.APPROVED)
                .values_list("doctor_id", flat=True))


def _publish(service: Service, event_type: str) -> None:
    for doctor_id in _affected_doctor_ids(service):
        publish_doctor_event(doctor_id, event_type, {"service_id": str(service.id)})


def create(*, owner_doctor: Doctor | None = None, owner_clinic_id: UUID | None = None, data: dict) -> Service:
    spec_ids = set(owner_doctor.specializations.values_list("id", flat=True)) if owner_doctor else set()
    _validate(data, doctor=owner_doctor, specialization_ids=spec_ids)
    if owner_clinic_id and data.get("place") == Service.Place.HOME:
        raise OfferingError("Klinika xizmati uyda bo'lmaydi")
    with transaction.atomic():
        service = Service.objects.create(doctor=owner_doctor, clinic_id=owner_clinic_id, **data)
        _publish(service, "ServicePriceChanged")
    return service


def update(service: Service, data: dict, *, user, confirm_large_change: bool = False) -> Service:
    doctor = service.doctor
    spec_ids = set(doctor.specializations.values_list("id", flat=True)) if doctor else set()
    _validate(data, doctor=doctor, specialization_ids=spec_ids)

    new_price = data.get("price")
    large = bool(new_price is not None and new_price > service.price * LARGE_INCREASE_RATIO)
    if large and not confirm_large_change:
        raise LargePriceChange(service.price, new_price)

    with transaction.atomic():
        locked = Service.objects.select_for_update().get(id=service.id)
        if new_price is not None and new_price != locked.price:
            ServicePriceHistory.objects.create(service=locked, old_price=locked.price, new_price=new_price,
                                               changed_by=user, is_large_change=large)
        before = {f: getattr(locked, f) for f in data}
        for field, value in data.items():
            setattr(locked, field, value)
        locked.save()
        audit.record("service.update", obj=locked, actor=user, before=before, after=dict(data))
        _publish(locked, "ServicePriceChanged" if new_price is not None else "DoctorUpdated")
    return locked


def toggle(service: Service) -> Service:
    with transaction.atomic():
        Service.objects.filter(id=service.id).update(is_active=not service.is_active, updated_at=timezone.now())
        service.refresh_from_db()
        _publish(service, "DoctorUpdated")
    return service


def delete(service: Service) -> str:
    """'deleted' | 'deactivated'."""
    with transaction.atomic():
        if BookingItem.objects.filter(service=service).exists():
            Service.objects.filter(id=service.id).update(is_active=False, updated_at=timezone.now())
            outcome = "deactivated"
        else:
            doctor_ids = _affected_doctor_ids(service)
            service.delete()
            for doctor_id in doctor_ids:
                publish_doctor_event(doctor_id, "DoctorUpdated")
            return "deleted"
        service.refresh_from_db()
        _publish(service, "DoctorUpdated")
    return outcome


PUBLIC_PROFILE_FIELDS = (
    "bio", "languages", "accepts_home_visits", "home_visit_radius_km", "education",
    "home_base_latitude", "home_base_longitude",  # C4
    "follow_up_discount_percent",  # C12
    # B10: shifokor klinikadagi qabul uchun to'lov rejimini o'zi tanlaydi —
    # no-show riskini u oladi, konversiya foydasini ham u ko'radi
    "clinic_payment_mode",
)


def update_public_profile(doctor: Doctor, data: dict) -> Doctor:
    """Tasdiqlangan shifokor moderatsiyasiz o'zgartira oladigan maydonlar.

    Litsenziya, mutaxassislik, ism — o'zgarsa qayta moderatsiya kerak, bu yerda YO'Q.
    """
    unknown = set(data) - set(PUBLIC_PROFILE_FIELDS)
    if unknown:
        raise OfferingError(f"Bu maydonlar moderatsiyasiz o'zgarmaydi: {', '.join(sorted(unknown))}")
    with transaction.atomic():
        for field, value in data.items():
            setattr(doctor, field, value)
        doctor.save()
        publish_doctor_event(doctor.id, "DoctorUpdated")
    return doctor
