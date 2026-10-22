"""Kiruvchi eventlar → proyeksiya → ES hujjatlari (consumer tranzaksiyasi ichida, idempotent).

Har jihat o'z vaqtiga ega: event `occurred_at` i shu jihatning oxirgi vaqtidan ESKI bo'lsa, o'zgarish
qo'llanmaydi (masalan, kechikib kelgan `doctor.suspended` keyingi `doctor.reinstated` ni bekor qilmaydi).
Hujjat baribir qayta quriladi — natija bir xil, zarari yo'q.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics import STALE_EVENTS
from app.models import AffiliationRow, ServiceRow
from app.models.projection import APPROVED, SUSPENDED
from app.repositories import projection as repo
from app.services import documents

AFFILIATION_STATUS = {"affiliation.accepted": "active", "affiliation.resumed": "active", "affiliation.paused": "paused"}


def _fresh(current: datetime, occurred_at: datetime, aspect: str) -> bool:
    if occurred_at < current:
        STALE_EVENTS.labels(aspect=aspect).inc()
        return False
    return True


def _uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


# --------------------------------------------------------------------------- doctor


async def on_doctor_profile(session: AsyncSession, data: dict[str, Any], at: datetime, *, approved: bool) -> None:
    """`doctor.approved` (+ holat) va `doctor.profile_updated`."""
    d = await repo.lock_doctor(session, uuid.UUID(data["doctor_id"]))
    if _fresh(d.profile_at, at, "doctor_profile"):
        d.user_id = _uuid(data.get("user_id")) or d.user_id
        d.full_name = data["full_name"]
        d.specialization_ids = [str(x) for x in data["specialization_ids"]]
        d.accepts_home_visits = bool(data["accepts_home_visits"])
        for field in ("experience_years", "home_visit_radius_km"):
            if field in data:
                setattr(d, field, int(data[field]))
        if "languages" in data:
            d.languages = list(data["languages"])
        if "photo_file_id" in data:
            d.photo_file_id = _uuid(data["photo_file_id"])
        d.profile_at = at
    if approved and _fresh(d.status_at, at, "doctor_status"):
        d.status, d.status_at = APPROVED, at
    await documents.publish(session, doctor_ids=[d.id], clinic_ids=await _clinics_of(session, d.id))


async def on_doctor_status(session: AsyncSession, data: dict[str, Any], at: datetime, *, status: str) -> None:
    """`doctor.suspended` (yashirish — o'chirmaymiz: "nega yo'qoldi" savoliga javob) / `doctor.reinstated`."""
    d = await repo.lock_doctor(session, uuid.UUID(data["doctor_id"]))
    if _fresh(d.status_at, at, "doctor_status"):
        d.status, d.status_at = status, at
    await documents.publish(session, doctor_ids=[d.id], clinic_ids=await _clinics_of(session, d.id))


async def on_availability(session: AsyncSession, data: dict[str, Any], at: datetime) -> None:
    d = await repo.lock_doctor(session, uuid.UUID(data["doctor_id"]))
    if _fresh(d.availability_at, at, "availability"):
        raw = data.get("next_available_at")
        d.next_available_at = datetime.fromisoformat(raw) if raw else None
        d.availability_at = at
    await documents.publish(session, doctor_ids=[d.id])


async def on_rating(session: AsyncSession, data: dict[str, Any], at: datetime) -> None:
    d = await repo.lock_doctor(session, uuid.UUID(data["doctor_id"]))
    if _fresh(d.rating_at, at, "doctor_rating"):
        d.rating = data["rating"]
        d.reviews_count = int(data["reviews_count"])
        d.rating_at = at
    clinic_ids: list[uuid.UUID] = []
    if data.get("clinic_id"):
        c = await repo.lock_clinic(session, uuid.UUID(data["clinic_id"]))
        if _fresh(c.rating_at, at, "clinic_rating"):
            c.rating = data.get("clinic_rating")
            c.reviews_count = int(data.get("clinic_reviews_count") or 0)
            c.rating_at = at
        clinic_ids.append(c.id)
    # Klinika reytingi shifokor hujjatida yo'q — faqat klinika hujjati qayta quriladi
    await documents.publish(session, doctor_ids=[d.id], clinic_ids=clinic_ids)


# --------------------------------------------------------------------------- clinic


async def on_clinic_info(session: AsyncSession, data: dict[str, Any], at: datetime, *, status: str | None) -> None:
    """`clinic.published` (→ active) va `clinic.updated` (holat ham keladi)."""
    c = await repo.lock_clinic(session, uuid.UUID(data["clinic_id"]))
    if _fresh(c.info_at, at, "clinic_info"):
        c.name, c.city, c.street = data["name"], data["city"], data["street"]
        c.lat, c.lng = float(data["latitude"]), float(data["longitude"])
        c.info_at = at
    if status and _fresh(c.status_at, at, "clinic_status"):
        c.status, c.status_at = status, at
    await documents.publish(session, doctor_ids=await repo.doctor_ids_of_clinic(session, c.id), clinic_ids=[c.id])


async def on_clinic_suspended(session: AsyncSession, data: dict[str, Any], at: datetime) -> None:
    c = await repo.lock_clinic(session, uuid.UUID(data["clinic_id"]))
    if _fresh(c.status_at, at, "clinic_status"):
        c.status, c.status_at = SUSPENDED, at
    await documents.publish(session, doctor_ids=await repo.doctor_ids_of_clinic(session, c.id), clinic_ids=[c.id])


# --------------------------------------------------------------------------- affiliation


async def on_affiliation(session: AsyncSession, event_type: str, data: dict[str, Any], at: datetime) -> None:
    status = AFFILIATION_STATUS.get(event_type, "ended")
    affiliation_id = uuid.UUID(data["affiliation_id"])
    doctor_id, clinic_id = uuid.UUID(data["doctor_id"]), uuid.UUID(data["clinic_id"])
    row = await repo.get_affiliation(session, affiliation_id)
    if row is None:
        session.add(AffiliationRow(id=affiliation_id, doctor_id=doctor_id, clinic_id=clinic_id, status=status, at=at))
    elif _fresh(row.at, at, "affiliation"):
        row.status, row.at = status, at
    await repo.lock_clinic(session, clinic_id)  # klinika qatori bo'lmasa — keyingi clinic.* eventi to'ldiradi
    await documents.publish(session, doctor_ids=[doctor_id], clinic_ids=[clinic_id])


# --------------------------------------------------------------------------- service


async def on_service(session: AsyncSession, data: dict[str, Any], at: datetime, *, deactivated: bool) -> None:
    service_id = uuid.UUID(data["service_id"])
    row = await repo.get_service(session, service_id)
    if row is None:
        row = ServiceRow(id=service_id, at=datetime.fromtimestamp(0, tz=at.tzinfo))
        session.add(row)
    if _fresh(row.at, at, "service"):
        row.doctor_id, row.clinic_id = _uuid(data.get("doctor_id")), _uuid(data.get("clinic_id"))
        if deactivated:
            row.is_active = False
        else:
            row.specialization_id = _uuid(data["specialization_id"])
            row.name, row.name_ru, row.place = data["name"], data.get("name_ru", ""), data["place"]
            row.price_tiyin, row.is_active = int(data["price_tiyin"]), bool(data["is_active"])
        row.at = at
    doctor_ids = [row.doctor_id] if row.doctor_id else []
    clinic_ids = [row.clinic_id] if row.clinic_id else []
    if row.clinic_id and not row.doctor_id:
        # Klinikaning o'z xizmati — undagi barcha shifokorlarning narxi/joyi o'zgaradi
        doctor_ids = await repo.doctor_ids_of_clinic(session, row.clinic_id)
    await documents.publish(session, doctor_ids=doctor_ids, clinic_ids=clinic_ids)


async def _clinics_of(session: AsyncSession, doctor_id: uuid.UUID) -> list[uuid.UUID]:
    """Shifokor o'zgarsa klinika hujjati (shifokorlar soni, mutaxassisliklar) ham o'zgaradi."""
    return sorted({a.clinic_id for a in await repo.affiliations_of_doctor(session, doctor_id)})
