"""O'qish: ommaviy katalog (A9 — faqat tasdiqlangan shifokor/faol klinika) va internal API ma'lumotlari.

Qidiruv Postgres'da (to'liq matnli qidiruv — search-service, 5-bosqich). Geo filtr: bounding box SQL'da,
aniq masofa (haversine) Python'da.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.geo import bounding_box, haversine_km

from app.core.config import get_settings
from app.models import (
    Affiliation,
    Clinic,
    ClinicMembership,
    Doctor,
    DoctorSpecialization,
    Service,
    Specialization,
)
from app.repositories import catalog as repo
from app.services.errors import DoctorNotFound

MAX_GEO_CANDIDATES = 2000


@dataclass(frozen=True)
class PublicRating:
    rating: Decimal | None
    reviews_count: int
    is_new: bool


def public_rating(rating: Decimal | None, reviews_count: int) -> PublicRating:
    """C11: 5 tadan kam sharhda reyting ko'rsatilmaydi (2–3 ta sharh adolatsiz bo'lishi mumkin)."""
    hidden = reviews_count < get_settings().min_reviews_for_rating
    return PublicRating(rating=None if hidden else rating, reviews_count=reviews_count, is_new=hidden)


def _approved() -> Select[Doctor]:
    return select(Doctor).where(Doctor.status == "approved")


@dataclass(frozen=True)
class DoctorRow:
    doctor: Doctor
    specializations: list[Specialization]
    distance_km: float | None


async def list_doctors(
    session: AsyncSession,
    *,
    specialization_id: uuid.UUID | None,
    clinic_id: uuid.UUID | None,
    home_only: bool,
    lat: float | None,
    lng: float | None,
    radius_km: float,
    q: str | None,
    limit: int,
    offset: int,
) -> list[DoctorRow]:
    stmt = _approved()
    if specialization_id:
        stmt = stmt.where(
            exists().where(
                DoctorSpecialization.doctor_id == Doctor.id, DoctorSpecialization.specialization_id == specialization_id
            )
        )
    if clinic_id:
        in_clinic = (
            select(Affiliation.doctor_id)
            .join(Clinic, Clinic.id == Affiliation.clinic_id)
            .where(Affiliation.clinic_id == clinic_id, Affiliation.status == "active", Clinic.status == "active")
        )
        stmt = stmt.where(Doctor.id.in_(in_clinic))
    if home_only:
        stmt = stmt.where(Doctor.accepts_home_visits.is_(True))
    if q:
        stmt = stmt.where(Doctor.full_name.ilike(f"%{q.strip()}%"))
    stmt = stmt.order_by(Doctor.rating.desc().nulls_last(), Doctor.reviews_count.desc(), Doctor.id)

    distances: dict[uuid.UUID, float] = {}
    if lat is not None and lng is not None:
        if home_only:
            # Uy chaqiruvi: nuqta shifokorning O'Z radiusi ichida (bazadan)
            min_lat, min_lng, max_lat, max_lng = bounding_box(lat, lng, 100)
            stmt = stmt.where(
                Doctor.home_base_latitude.between(min_lat, max_lat),
                Doctor.home_base_longitude.between(min_lng, max_lng),
            )
            candidates = (await session.scalars(stmt.limit(MAX_GEO_CANDIDATES))).all()
            for d in candidates:
                assert d.home_base_latitude is not None and d.home_base_longitude is not None
                km = haversine_km(lat, lng, float(d.home_base_latitude), float(d.home_base_longitude))
                if km <= d.home_visit_radius_km:
                    distances[d.id] = km
            doctors = [d for d in candidates if d.id in distances]
        else:
            # Klinikada: faol klinikasi radius ichida bo'lgan shifokorlar, eng yaqini birinchi
            min_lat, min_lng, max_lat, max_lng = bounding_box(lat, lng, radius_km)
            clinic_rows = (
                await session.execute(
                    select(Affiliation.doctor_id, Clinic.latitude, Clinic.longitude)
                    .join(Clinic, Clinic.id == Affiliation.clinic_id)
                    .where(
                        Affiliation.status == "active",
                        Clinic.status == "active",
                        Clinic.latitude.between(min_lat, max_lat),
                        Clinic.longitude.between(min_lng, max_lng),
                    )
                )
            ).all()
            for doctor_id, clat, clng in clinic_rows:
                km = haversine_km(lat, lng, float(clat), float(clng))
                if km <= radius_km and km < distances.get(doctor_id, float("inf")):
                    distances[doctor_id] = km
            if not distances:
                return []
            candidates = (await session.scalars(stmt.where(Doctor.id.in_(distances)))).all()
            doctors = sorted(candidates, key=lambda d: distances[d.id])
        page = doctors[offset : offset + limit]
    else:
        page = list((await session.scalars(stmt.limit(limit).offset(offset))).all())
    specs = await repo.specializations_by_doctor(session, [d.id for d in page])
    return [DoctorRow(d, specs.get(d.id, []), round(distances[d.id], 2) if d.id in distances else None) for d in page]


async def public_doctor(
    session: AsyncSession, doctor_id: uuid.UUID
) -> tuple[Doctor, list[Specialization], list[Clinic]]:
    doctor = await repo.get_doctor(session, doctor_id)
    if doctor is None or doctor.status != "approved":
        raise DoctorNotFound()  # A9: tasdiqlanmagan — xuddi yo'qdek
    specs = (await repo.specializations_by_doctor(session, [doctor.id])).get(doctor.id, [])
    clinic_ids = await repo.active_clinic_ids(session, doctor.id)
    clinics = list((await session.scalars(select(Clinic).where(Clinic.id.in_(clinic_ids)))).all()) if clinic_ids else []
    return doctor, specs, clinics


async def public_doctor_services(session: AsyncSession, doctor_id: uuid.UUID, place: str | None) -> Sequence[Service]:
    """ADR-0011 #10: faqat tasdiqlangan shifokor (monolitda status tekshirilmasdi)."""
    doctor = await repo.get_doctor(session, doctor_id)
    if doctor is None or doctor.status != "approved":
        raise DoctorNotFound()
    stmt = repo.bookable_services_query(doctor_id)
    if place:
        stmt = stmt.where(Service.place == place)
    return (await session.scalars(stmt.order_by(Service.price_tiyin, Service.name))).all()


async def list_clinics(session: AsyncSession, city: str | None, limit: int, offset: int) -> Sequence[Clinic]:
    stmt = select(Clinic).where(Clinic.status == "active")
    if city:
        stmt = stmt.where(func.lower(Clinic.city) == city.strip().lower())
    stmt = stmt.order_by(Clinic.rating.desc().nulls_last(), Clinic.reviews_count.desc(), Clinic.name)
    return (await session.scalars(stmt.limit(limit).offset(offset))).all()


# --- internal --------------------------------------------------------------------------------


async def services_by_ids(
    session: AsyncSession, ids: Sequence[uuid.UUID], bookable_for: uuid.UUID | None
) -> list[tuple[Service, Specialization]]:
    if not ids:
        return []
    base = repo.bookable_services_query(bookable_for) if bookable_for else select(Service)
    rows = (await session.scalars(base.where(Service.id.in_(set(ids))))).all()
    specs = await repo.specializations_map(session, [s.specialization_id for s in rows])
    return [(s, specs[s.specialization_id]) for s in rows]


async def access(
    session: AsyncSession, user_id: uuid.UUID, doctor_id: uuid.UUID | None, clinic_id: uuid.UUID | None
) -> tuple[bool, str | None, list[uuid.UUID]]:
    clinic_ids = await repo.managed_clinic_ids(session, user_id)
    if doctor_id is not None:
        doctor = await repo.get_doctor(session, doctor_id)
        if doctor is not None and doctor.user_id == user_id:
            return True, "doctor", clinic_ids
        return False, None, clinic_ids
    if clinic_id is not None:
        allowed = clinic_id in clinic_ids
        return allowed, "clinic_admin" if allowed else None, clinic_ids
    return False, None, clinic_ids


async def has_membership(session: AsyncSession, user_id: uuid.UUID) -> bool:
    stmt = select(ClinicMembership.id).where(ClinicMembership.user_id == user_id, ClinicMembership.is_active.is_(True))
    return (await session.execute(stmt.limit(1))).first() is not None
