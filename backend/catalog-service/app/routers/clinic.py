"""/v1/clinic/{clinic_id} — klinika admini (user JWT). A'zolik DB'dan tekshiriladi; boshqarmaydigan klinika — 404."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from medbron_common.db import transaction

from app.models import Clinic
from app.repositories import catalog as repo
from app.routers.convert import affiliation_out, clinic_admin_out
from app.routers.deps import Files, Ip, Session, User
from app.schemas.catalog import (
    AffiliationOut,
    ClinicAdminOut,
    ClinicDoctorOut,
    ClinicProfileUpdate,
    InviteIn,
    RoomIn,
    RoomOut,
    RoomUpdate,
    ServiceIn,
    ServiceOut,
    ServiceUpdate,
)
from app.services import affiliations, clinics, offerings
from app.services.errors import AffiliationNotFound

router = APIRouter(prefix="/v1/clinic", tags=["clinic"])


@router.get("/mine", response_model=list[ClinicAdminOut])
async def my_clinics(user: User, session: Session) -> list[ClinicAdminOut]:
    ids = await repo.managed_clinic_ids(session, user.user_id)
    rows = (await session.scalars(select(Clinic).where(Clinic.id.in_(ids)).order_by(Clinic.name))).all() if ids else []
    return [clinic_admin_out(c) for c in rows]


@router.get("/{clinic_id}/profile", response_model=ClinicAdminOut)
async def profile(clinic_id: UUID, user: User, session: Session) -> ClinicAdminOut:
    return clinic_admin_out(await clinics.require_managed(session, user.user_id, clinic_id))


@router.patch("/{clinic_id}/profile", response_model=ClinicAdminOut)
async def update_profile(
    clinic_id: UUID, body: ClinicProfileUpdate, user: User, session: Session, files: Files, ip: Ip
) -> ClinicAdminOut:
    """Faqat tavsif, telefon, ish vaqti, rasm. Nom/manzil/koordinata — admin moderatsiyasi orqali."""
    async with transaction(session):
        clinic = await clinics.update_profile(
            session, files, user.user_id, clinic_id, body.model_dump(exclude_unset=True), ip=ip
        )
    return clinic_admin_out(clinic)


@router.get("/{clinic_id}/rooms", response_model=list[RoomOut])
async def rooms(clinic_id: UUID, user: User, session: Session) -> list[RoomOut]:
    return [RoomOut.model_validate(r) for r in await clinics.rooms(session, user.user_id, clinic_id)]


@router.post("/{clinic_id}/rooms", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
async def create_room(clinic_id: UUID, body: RoomIn, user: User, session: Session, ip: Ip) -> RoomOut:
    async with transaction(session):
        room = await clinics.create_room(session, user.user_id, clinic_id, body.model_dump(), ip=ip)
    return RoomOut.model_validate(room)


@router.patch("/{clinic_id}/rooms/{room_id}", response_model=RoomOut)
async def update_room(
    clinic_id: UUID, room_id: UUID, body: RoomUpdate, user: User, session: Session, ip: Ip
) -> RoomOut:
    async with transaction(session):
        room = await clinics.update_room(
            session, user.user_id, clinic_id, room_id, body.model_dump(exclude_unset=True), ip=ip
        )
    return RoomOut.model_validate(room)


@router.get("/{clinic_id}/doctors", response_model=list[ClinicDoctorOut])
async def doctors(clinic_id: UUID, user: User, session: Session) -> list[ClinicDoctorOut]:
    rows = await affiliations.clinic_doctors(session, user.user_id, clinic_id)
    return [
        ClinicDoctorOut(**affiliation_out(a).model_dump(), doctor_name=d.full_name, doctor_status=d.status)
        for a, d in rows
    ]


@router.post("/{clinic_id}/doctors/invite", response_model=AffiliationOut, status_code=status.HTTP_201_CREATED)
async def invite(clinic_id: UUID, body: InviteIn, user: User, session: Session, ip: Ip) -> AffiliationOut:
    async with transaction(session):
        aff = await affiliations.invite(session, user.user_id, clinic_id, body.doctor_id, body.position, ip=ip)
    return affiliation_out(aff)


@router.post("/{clinic_id}/doctors/{affiliation_id}/{action}", response_model=AffiliationOut)
async def affiliation_action(
    clinic_id: UUID, affiliation_id: UUID, action: str, user: User, session: Session, ip: Ip
) -> AffiliationOut:
    """pause — slotlar yopiladi, bronlar saqlanadi; resume; end — kelajakdagi bronlar bekor (booking)."""
    if action not in affiliations.CLINIC_ACTIONS:
        raise AffiliationNotFound()
    async with transaction(session):
        aff = await affiliations.clinic_action(session, user.user_id, clinic_id, affiliation_id, action, ip=ip)
    return affiliation_out(aff)


@router.get("/{clinic_id}/services", response_model=list[ServiceOut])
async def services(clinic_id: UUID, user: User, session: Session) -> list[ServiceOut]:
    return [ServiceOut.model_validate(s) for s in await offerings.clinic_services(session, user.user_id, clinic_id)]


@router.post("/{clinic_id}/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(clinic_id: UUID, body: ServiceIn, user: User, session: Session, ip: Ip) -> ServiceOut:
    async with transaction(session):
        service = await offerings.create(session, body.model_dump(), user_id=user.user_id, clinic_id=clinic_id, ip=ip)
    return ServiceOut.model_validate(service)


@router.patch("/{clinic_id}/services/{service_id}", response_model=ServiceOut)
async def update_service(
    clinic_id: UUID, service_id: UUID, body: ServiceUpdate, user: User, session: Session, ip: Ip
) -> ServiceOut:
    changes = body.model_dump(exclude_unset=True, exclude={"confirm_large_change"})
    async with transaction(session):
        service = await offerings.update(
            session,
            service_id,
            changes,
            user_id=user.user_id,
            clinic_id=clinic_id,
            confirm_large_change=body.confirm_large_change,
            ip=ip,
        )
    return ServiceOut.model_validate(service)


@router.delete("/{clinic_id}/services/{service_id}", response_model=ServiceOut)
async def deactivate_service(clinic_id: UUID, service_id: UUID, user: User, session: Session, ip: Ip) -> ServiceOut:
    async with transaction(session):
        service = await offerings.deactivate(session, service_id, user_id=user.user_id, clinic_id=clinic_id, ip=ip)
    return ServiceOut.model_validate(service)
