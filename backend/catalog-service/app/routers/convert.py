"""Model → javob sxemasi (bir nechta router ishlatadi)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Affiliation, Clinic, Doctor, Specialization
from app.repositories import catalog as repo
from app.schemas.catalog import (
    AffiliationOut,
    ClinicAdminOut,
    ClinicOut,
    DoctorListItem,
    DoctorPrivate,
    SpecializationBrief,
)
from app.services.queries import public_rating


def clinic_out(clinic: Clinic) -> ClinicOut:
    rating = public_rating(clinic.rating, clinic.reviews_count)
    return ClinicOut(
        id=clinic.id,
        name=clinic.name,
        description=clinic.description,
        phone=clinic.phone,
        city=clinic.city,
        street=clinic.street,
        latitude=clinic.latitude,
        longitude=clinic.longitude,
        working_hours=clinic.working_hours or {},
        photo_url=clinic.photo_url,
        rating=rating.rating,
        reviews_count=rating.reviews_count,
        is_new=rating.is_new,
    )


def clinic_admin_out(clinic: Clinic) -> ClinicAdminOut:
    return ClinicAdminOut(
        **clinic_out(clinic).model_dump(),
        status=clinic.status,
        status_reason=clinic.status_reason,
        created_at=clinic.created_at,
    )


def doctor_item(doctor: Doctor, specs: list[Specialization], distance_km: float | None = None) -> DoctorListItem:
    rating = public_rating(doctor.rating, doctor.reviews_count)
    return DoctorListItem(
        id=doctor.id,
        full_name=doctor.full_name,
        experience_years=doctor.experience_years,
        rating=rating.rating,
        reviews_count=rating.reviews_count,
        is_new=rating.is_new,
        specializations=[SpecializationBrief(id=s.id, name=s.name, name_ru=s.name_ru) for s in specs],
        accepts_home_visits=doctor.accepts_home_visits,
        photo_url=doctor.photo_url,
        distance_km=distance_km,
    )


async def doctor_private(session: AsyncSession, doctor: Doctor) -> DoctorPrivate:
    return DoctorPrivate(
        id=doctor.id,
        user_id=doctor.user_id,
        status=doctor.status,
        status_reason=doctor.status_reason,
        full_name=doctor.full_name,
        bio=doctor.bio,
        education=doctor.education,
        experience_years=doctor.experience_years,
        languages=list(doctor.languages),
        license_number=doctor.license_number,
        license_expires_at=doctor.license_expires_at,
        accepts_home_visits=doctor.accepts_home_visits,
        home_visit_radius_km=doctor.home_visit_radius_km,
        home_base_latitude=doctor.home_base_latitude,
        home_base_longitude=doctor.home_base_longitude,
        clinic_payment_mode=doctor.clinic_payment_mode,
        follow_up_discount_percent=doctor.follow_up_discount_percent,
        photo_url=doctor.photo_url,
        specialization_ids=await repo.specialization_ids(session, doctor.id),
        submitted_at=doctor.submitted_at,
    )


def affiliation_out(aff: Affiliation) -> AffiliationOut:
    return AffiliationOut(
        affiliation_id=aff.id,
        clinic_id=aff.clinic_id,
        doctor_id=aff.doctor_id,
        status=aff.status,
        position=aff.position,
        updated_at=aff.updated_at,
    )


__all__ = ["affiliation_out", "clinic_admin_out", "clinic_out", "doctor_item", "doctor_private"]
