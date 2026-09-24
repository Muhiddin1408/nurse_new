"""
catalog/events.py — katalog domeni outbox hodisalari (E7).

Topic `catalog.events`, partition kaliti `doctor_id` — bitta shifokor
hodisalari tartibda. `api/search/projector.py` shularni Elasticsearch'ga
proyeksiya qiladi; ilgari bu hodisalar hech qayerda chiqarilmasdi va qidiruv
faqat qo'lda `reindex_doctors` bilan to'lardi.

Hodisalar: DoctorApproved, DoctorSuspended, DoctorUpdated, ServicePriceChanged
Payload — ES hujjatining TO'LIQ surati (projector bazaga qaytib bormaydi).
"""

from __future__ import annotations

from uuid import UUID

from api.events import services as events
from apps.catalog.models import Doctor

CATALOG_EVENTS_TOPIC = "catalog.events"


def build_doctor_payload(doctor: Doctor) -> dict:
    services = [s for s in doctor.services.all() if s.is_active]
    affiliations = [a for a in doctor.affiliations.select_related("clinic").all() if a.is_active]
    location = None
    for a in affiliations:
        location = {"lat": float(a.clinic.latitude), "lon": float(a.clinic.longitude)}
        break
    specs = list(doctor.specializations.all())
    return {
        "id": str(doctor.id),
        "full_name": doctor.user.full_name,
        "specialization_ids": [str(s.id) for s in specs],
        "specialization_names": [s.name for s in specs],
        "experience_years": doctor.experience_years,
        "rating": float(doctor.rating),
        "reviews_count": doctor.reviews_count,
        "accepts_home_visits": doctor.accepts_home_visits,
        "min_price": int(min((s.price for s in services), default=0)),
        "clinic_ids": [str(a.clinic_id) for a in affiliations],
        "location": location,
        "is_bookable": doctor.status == Doctor.Status.APPROVED and doctor.user.is_active,
        "status": doctor.status,
    }


def publish_doctor_event(doctor_id: UUID, event_type: str, extra: dict | None = None) -> None:
    """Chaqiruvchining tranzaksiyasi ICHIDA (transactional outbox)."""
    doctor = Doctor.objects.select_related("user").get(id=doctor_id)
    payload = build_doctor_payload(doctor)
    if extra:
        payload.update(extra)
    events.publish(topic=CATALOG_EVENTS_TOPIC, key=str(doctor_id), event_type=event_type, payload=payload)
