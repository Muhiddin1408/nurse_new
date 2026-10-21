"""Umumiy yordamchilar: event payload'lari (bitta joyda — kontrakt shakli takrorlanmaydi), aktyorlar."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.events import Events

from app.events import outbox
from app.models import Clinic, Doctor, Service
from app.repositories import catalog as repo

# Tizim (cron) amallari uchun aktyor ID — kontraktlarda `*_by` UUID bo'lishi shart
SYSTEM_ACTOR = uuid.UUID(int=0)


def as_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def clinic_payload(clinic: Clinic) -> dict[str, Any]:
    return {
        "clinic_id": clinic.id,
        "name": clinic.name,
        "city": clinic.city,
        "street": clinic.street,
        "latitude": float(clinic.latitude),
        "longitude": float(clinic.longitude),
        "working_hours": clinic.working_hours or {},
    }


def emit_clinic_updated(session: AsyncSession, clinic: Clinic) -> None:
    outbox.add_event(
        session, Events.CLINIC_UPDATED, aggregate_id=clinic.id, data={**clinic_payload(clinic), "status": clinic.status}
    )


async def emit_doctor_profile_updated(session: AsyncSession, doctor: Doctor) -> None:
    outbox.add_event(
        session,
        Events.DOCTOR_PROFILE_UPDATED,
        aggregate_id=doctor.id,
        data={
            "doctor_id": doctor.id,
            "user_id": doctor.user_id,
            "full_name": doctor.full_name,
            "specialization_ids": await repo.specialization_ids(session, doctor.id),
            "experience_years": doctor.experience_years,
            "languages": list(doctor.languages),
            "accepts_home_visits": doctor.accepts_home_visits,
            "home_visit_radius_km": doctor.home_visit_radius_km,
            "photo_file_id": doctor.photo_file_id,
        },
    )


def service_payload(service: Service) -> dict[str, Any]:
    return {
        "service_id": service.id,
        "clinic_id": service.clinic_id,
        "doctor_id": service.doctor_id,
        "specialization_id": service.specialization_id,
        "name": service.name,
        "name_ru": service.name_ru,
        "place": service.place,
        "duration_minutes": service.duration_minutes,
        "price_tiyin": service.price_tiyin,
        "is_active": service.is_active,
    }
