"""/internal/v1/catalog — faqat servislararo, `X-Internal-Token` bilan. Kong tashqariga OCHMAYDI.

Ruxsat etilgan chaqiruvchilar — docs/SERVICE_REGISTRY.md dagi ro'yxat bilan bir xil bo'lishi shart.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from medbron_common.errors import Forbidden

from app.core.security import verify_internal_token
from app.repositories import catalog as repo
from app.repositories import export as export_repo
from app.routers.deps import Session
from app.schemas.catalog import (
    AccessOut,
    Bookability,
    ExportAffiliation,
    ExportClinic,
    ExportClinics,
    ExportDoctor,
    ExportDoctors,
    ExportService,
    ExportSpecialization,
    ExportSpecializations,
    InternalClinic,
    InternalRoom,
    InternalService,
    InternalServices,
    PatientRules,
    Point,
)
from app.services import queries
from app.services.errors import ClinicNotFound, DoctorNotFound, InvalidRequest, RoomNotFound

MAX_IDS = 20

ALLOWED_CALLERS: tuple[str, ...] = (
    "billing-service",
    "booking-service",
    "pricing-service",
    "schedule-service",
    "search-service",
)
EXPORT_MAX_LIMIT = 500


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Xavfsiz standart: ro'yxat bo'sh bo'lsa — hech kim chaqira olmaydi.
    dependencies=[Depends(verify_internal_token(*ALLOWED_CALLERS) if ALLOWED_CALLERS else _deny_all)],
)


def _ids(raw: str) -> list[UUID]:
    try:
        ids = [UUID(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise InvalidRequest("ids — vergul bilan ajratilgan UUID'lar") from exc
    if not ids or len(ids) > MAX_IDS:
        raise InvalidRequest(f"ids: 1–{MAX_IDS} ta")
    return ids


@router.get("/doctors/{doctor_id}/bookability", response_model=Bookability)
async def bookability(doctor_id: UUID, session: Session) -> Bookability:
    doctor = await repo.get_doctor(session, doctor_id)
    if doctor is None:
        raise DoctorNotFound()
    home_base = (
        Point(lat=float(doctor.home_base_latitude), lng=float(doctor.home_base_longitude))
        if doctor.home_base_latitude is not None and doctor.home_base_longitude is not None
        else None
    )
    return Bookability(
        doctor_id=doctor.id,
        user_id=doctor.user_id,
        status=doctor.status,
        bookable=doctor.status == "approved",
        full_name=doctor.full_name,
        accepts_home_visits=doctor.accepts_home_visits,
        home_visit_radius_km=doctor.home_visit_radius_km,
        home_base=home_base,
        clinic_payment_mode=doctor.clinic_payment_mode,
        follow_up_discount_percent=doctor.follow_up_discount_percent,
        specialization_ids=await repo.specialization_ids(session, doctor.id),
        clinic_ids=await repo.active_clinic_ids(session, doctor.id),
    )


@router.get("/services", response_model=InternalServices)
async def services(session: Session, ids: str, bookable_for: UUID | None = None) -> InternalServices:
    rows = await queries.services_by_ids(session, _ids(ids), bookable_for)
    items = [
        InternalService(
            id=s.id,
            doctor_id=s.doctor_id,
            clinic_id=s.clinic_id,
            specialization_id=s.specialization_id,
            name=s.name,
            name_ru=s.name_ru,
            place=s.place,
            duration_minutes=s.duration_minutes,
            price_tiyin=s.price_tiyin,
            is_active=s.is_active,
            mxik_code=s.mxik_code,
            package_code=s.package_code,
            vat_percent=s.vat_percent,
            patient_rules=PatientRules(
                is_pediatric=spec.is_pediatric,
                accepts_children=spec.accepts_children,
                min_patient_age=spec.min_patient_age,
                max_patient_age=spec.max_patient_age,
                allowed_gender=spec.allowed_gender,
            ),
        )
        for s, spec in rows
    ]
    return InternalServices(items=items)


@router.get("/access", response_model=AccessOut)
async def access(
    session: Session, user_id: UUID, doctor_id: UUID | None = None, clinic_id: UUID | None = None
) -> AccessOut:
    allowed, role, clinic_ids = await queries.access(session, user_id, doctor_id, clinic_id)
    return AccessOut(allowed=allowed, role=role, clinic_ids=clinic_ids)


@router.get("/clinics/{clinic_id}", response_model=InternalClinic)
async def clinic(clinic_id: UUID, session: Session) -> InternalClinic:
    row = await repo.get_clinic(session, clinic_id)
    if row is None:
        raise ClinicNotFound()
    return InternalClinic(
        id=row.id,
        name=row.name,
        city=row.city,
        street=row.street,
        latitude=float(row.latitude),
        longitude=float(row.longitude),
        status=row.status,
        working_hours=row.working_hours or {},
    )


@router.get("/rooms/{room_id}", response_model=InternalRoom)
async def room(room_id: UUID, session: Session) -> InternalRoom:
    row = await repo.get_room(session, room_id)
    if row is None:
        raise RoomNotFound()
    return InternalRoom.model_validate(row)


# --- search-service reindex (2026-10-21 qarori): keyset sahifalash, faqat ommaviy maydonlar -----------------

ExportLimit = Annotated[int, Query(ge=1, le=EXPORT_MAX_LIMIT)]


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


@router.get("/export/doctors", response_model=ExportDoctors)
async def export_doctors(session: Session, after: UUID | None = None, limit: ExportLimit = 200) -> ExportDoctors:
    """`approved` va `suspended` shifokorlar (qidiruvda bo'lgan yoki bo'lishi mumkin bo'lganlar)."""
    rows = await export_repo.doctors_page(session, after, limit)
    ids = [d.id for d in rows]
    specs = await export_repo.specialization_ids_for(session, ids)
    affiliations = await export_repo.affiliations_for(session, ids)
    services = await export_repo.services_for(session, doctor_ids=ids)
    items = [
        ExportDoctor(
            doctor_id=d.id,
            user_id=d.user_id,
            full_name=d.full_name,
            status=d.status,
            specialization_ids=sorted(specs.get(d.id, []), key=str),
            experience_years=d.experience_years,
            languages=list(d.languages or []),
            accepts_home_visits=d.accepts_home_visits,
            home_visit_radius_km=d.home_visit_radius_km,
            home_base=(
                Point(lat=float(d.home_base_latitude), lng=float(d.home_base_longitude))
                if d.home_base_latitude is not None and d.home_base_longitude is not None
                else None
            ),
            photo_file_id=d.photo_file_id,
            rating=_float(d.rating),
            reviews_count=d.reviews_count,
            affiliations=[
                ExportAffiliation(affiliation_id=a.id, clinic_id=a.clinic_id, status=a.status)
                for a in affiliations.get(d.id, [])
            ],
            services=[ExportService.model_validate(s) for s in services.get(d.id, [])],
        )
        for d in rows
    ]
    return ExportDoctors(items=items, next_after=rows[-1].id if len(rows) == limit else None)


@router.get("/export/clinics", response_model=ExportClinics)
async def export_clinics(session: Session, after: UUID | None = None, limit: ExportLimit = 200) -> ExportClinics:
    rows = await export_repo.clinics_page(session, after, limit)
    services = await export_repo.services_for(session, clinic_ids=[c.id for c in rows])
    items = [
        ExportClinic(
            clinic_id=c.id,
            name=c.name,
            city=c.city,
            street=c.street,
            latitude=float(c.latitude),
            longitude=float(c.longitude),
            status=c.status,
            working_hours=c.working_hours or {},
            rating=_float(c.rating),
            reviews_count=c.reviews_count,
            services=[ExportService.model_validate(s) for s in services.get(c.id, [])],
        )
        for c in rows
    ]
    return ExportClinics(items=items, next_after=rows[-1].id if len(rows) == limit else None)


@router.get("/export/specializations", response_model=ExportSpecializations)
async def export_specializations(session: Session) -> ExportSpecializations:
    rows = await export_repo.specializations(session)
    return ExportSpecializations(items=[ExportSpecialization.model_validate(s) for s in rows])
