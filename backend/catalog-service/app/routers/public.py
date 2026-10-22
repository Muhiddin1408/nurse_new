"""/v1/catalog — ommaviy katalog (tokensiz). Faqat tasdiqlangan shifokorlar va faol klinikalar (A9)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.repositories import catalog as repo
from app.routers.convert import clinic_out, doctor_item
from app.routers.deps import Session
from app.schemas.catalog import ClinicOut, DoctorDetail, DoctorListItem, PublicServiceOut, SpecializationOut
from app.services import queries, specializations
from app.services.errors import ClinicNotFound, InvalidRequest

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])


@router.get("/specializations", response_model=list[SpecializationOut])
async def list_specializations(session: Session) -> list[SpecializationOut]:
    return [SpecializationOut.model_validate(s) for s in await specializations.list_all(session)]


@router.get("/clinics", response_model=list[ClinicOut])
async def list_clinics(
    session: Session,
    city: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ClinicOut]:
    return [clinic_out(c) for c in await queries.list_clinics(session, city, limit, offset)]


@router.get("/clinics/{clinic_id}", response_model=ClinicOut)
async def get_clinic(clinic_id: UUID, session: Session) -> ClinicOut:
    clinic = await repo.get_clinic(session, clinic_id)
    if clinic is None or clinic.status != "active":
        raise ClinicNotFound()
    return clinic_out(clinic)


@router.get("/doctors", response_model=list[DoctorListItem])
async def list_doctors(
    session: Session,
    specialization: UUID | None = None,
    clinic: UUID | None = None,
    home_only: bool = False,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius: Annotated[float, Query(gt=0, le=50)] = 5,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DoctorListItem]:
    """`home_only` + lat/lng — nuqta shifokorning o'z uy chaqiruvi radiusi ichida; lat/lng (klinika) —
    faol klinikasi `radius` km ichida, eng yaqini birinchi (`distance_km`)."""
    if (lat is None) != (lng is None):
        raise InvalidRequest("lat va lng birga yuboriladi")
    rows = await queries.list_doctors(
        session,
        specialization_id=specialization,
        clinic_id=clinic,
        home_only=home_only,
        lat=lat,
        lng=lng,
        radius_km=radius,
        q=q,
        limit=limit,
        offset=offset,
    )
    return [doctor_item(r.doctor, r.specializations, r.distance_km) for r in rows]


@router.get("/doctors/{doctor_id}", response_model=DoctorDetail)
async def get_doctor(doctor_id: UUID, session: Session) -> DoctorDetail:
    doctor, specs, clinics = await queries.public_doctor(session, doctor_id)
    return DoctorDetail(
        **doctor_item(doctor, specs).model_dump(),
        bio=doctor.bio,
        education=doctor.education,
        languages=list(doctor.languages),
        license_number=doctor.license_number,
        home_visit_radius_km=doctor.home_visit_radius_km,
        clinics=[clinic_out(c) for c in clinics],
    )


@router.get("/doctors/{doctor_id}/services", response_model=list[PublicServiceOut])
async def doctor_services(
    doctor_id: UUID, session: Session, place: Literal["clinic", "home"] | None = None
) -> list[PublicServiceOut]:
    return [PublicServiceOut.model_validate(s) for s in await queries.public_doctor_services(session, doctor_id, place)]
