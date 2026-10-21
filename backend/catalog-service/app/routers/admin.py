"""/v1/admin — moderatsiya (`doctors.moderate`), klinikalar va mutaxassisliklar (`clinics.manage`)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select

from medbron_common.audit import Actor
from medbron_common.db import transaction

from app.core import permissions
from app.core.config import get_settings
from app.core.security import CurrentAdmin, require_permission
from app.models import Clinic, Doctor, DoctorDocument
from app.repositories import catalog as repo
from app.routers.convert import clinic_admin_out, doctor_private
from app.routers.deps import Files, Ip, Session
from app.schemas.catalog import (
    ClinicAdminOut,
    ClinicAdminUpdate,
    ClinicCreate,
    DocumentOut,
    MemberIn,
    MemberOut,
    ModerationDoctor,
    SignedUrlOut,
    SpecializationIn,
    SpecializationOut,
    SpecializationUpdate,
    StatusReason,
)
from app.services import clinics, doctors, specializations
from app.services.doctors import Moderator
from app.services.errors import ClinicNotFound, DoctorNotFound, DocumentNotFound

router = APIRouter(prefix="/v1/admin", tags=["admin"])

ModeratorDep = Annotated[CurrentAdmin, Depends(require_permission(permissions.DOCTORS_MODERATE))]
Manager = Annotated[CurrentAdmin, Depends(require_permission(permissions.CLINICS_MANAGE))]


async def _moderation_view(session: Session, doctor: Doctor) -> ModerationDoctor:
    base = await doctor_private(session, doctor)
    docs = [DocumentOut.model_validate(d) for d in await repo.documents(session, doctor.id)]
    return ModerationDoctor(
        **base.model_dump(), documents=docs, moderated_at=doctor.moderated_at, sla_due_at=doctors.sla_due_at(doctor)
    )


# --- moderatsiya -----------------------------------------------------------------------------


@router.get("/moderation/doctors", response_model=list[ModerationDoctor])
async def moderation_queue(
    _: ModeratorDep,
    session: Session,
    status_: Annotated[
        Literal["draft", "pending", "approved", "rejected", "suspended"], Query(alias="status")
    ] = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ModerationDoctor]:
    stmt = (
        select(Doctor)
        .where(Doctor.status == status_)
        .order_by(Doctor.submitted_at.asc().nulls_last(), Doctor.created_at)
        .limit(limit)
    )
    return [await _moderation_view(session, d) for d in (await session.scalars(stmt)).all()]


@router.get("/moderation/doctors/{doctor_id}", response_model=ModerationDoctor)
async def moderation_doctor(doctor_id: UUID, _: ModeratorDep, session: Session) -> ModerationDoctor:
    doctor = await repo.get_doctor(session, doctor_id)
    if doctor is None:
        raise DoctorNotFound()
    return await _moderation_view(session, doctor)


@router.get("/moderation/doctors/{doctor_id}/documents/{document_id}/url", response_model=SignedUrlOut)
async def document_url(
    doctor_id: UUID, document_id: UUID, admin: ModeratorDep, session: Session, files: Files
) -> SignedUrlOut:
    """≤ 5 daqiqalik presigned URL; ochilish file-service'da auditga yoziladi (moderator ID bilan)."""
    document = await session.get(DoctorDocument, document_id)
    if document is None or document.doctor_id != doctor_id:
        raise DocumentNotFound()
    signed = await files.signed_url(
        document.file_id, viewer_type="admin", viewer_id=admin.admin_id, ttl=get_settings().document_url_ttl_seconds
    )
    return SignedUrlOut(url=signed.url, expires_in=signed.expires_in)


@router.post("/moderation/doctors/{doctor_id}/{action}", response_model=ModerationDoctor)
async def moderate(
    doctor_id: UUID,
    action: Literal["approve", "reject", "suspend", "reinstate"],
    admin: ModeratorDep,
    session: Session,
    ip: Ip,
    body: StatusReason | None = None,
) -> ModerationDoctor:
    reason = body.reason if body else ""
    async with transaction(session):
        doctor = await doctors.moderate(
            session, doctor_id, action, Moderator(Actor.admin(admin), admin.admin_id), reason=reason, ip=ip
        )
    return await _moderation_view(session, doctor)


# --- klinikalar ------------------------------------------------------------------------------


@router.get("/clinics", response_model=list[ClinicAdminOut])
async def list_clinics(
    _: Manager,
    session: Session,
    status_: Annotated[Literal["draft", "active", "suspended"] | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ClinicAdminOut]:
    stmt = select(Clinic)
    if status_:
        stmt = stmt.where(Clinic.status == status_)
    rows = (await session.scalars(stmt.order_by(Clinic.created_at.desc()).limit(limit).offset(offset))).all()
    return [clinic_admin_out(c) for c in rows]


@router.post("/clinics", response_model=ClinicAdminOut, status_code=status.HTTP_201_CREATED)
async def create_clinic(body: ClinicCreate, admin: Manager, session: Session, ip: Ip) -> ClinicAdminOut:
    async with transaction(session):
        clinic = await clinics.create(session, body.model_dump(), actor=Actor.admin(admin), ip=ip)
    return clinic_admin_out(clinic)


@router.get("/clinics/{clinic_id}", response_model=ClinicAdminOut)
async def get_clinic(clinic_id: UUID, _: Manager, session: Session) -> ClinicAdminOut:
    clinic = await repo.get_clinic(session, clinic_id)
    if clinic is None:
        raise ClinicNotFound()
    return clinic_admin_out(clinic)


@router.patch("/clinics/{clinic_id}", response_model=ClinicAdminOut)
async def update_clinic(
    clinic_id: UUID, body: ClinicAdminUpdate, admin: Manager, session: Session, ip: Ip
) -> ClinicAdminOut:
    async with transaction(session):
        clinic = await clinics.admin_update(
            session, clinic_id, body.model_dump(exclude_unset=True), actor=Actor.admin(admin), ip=ip
        )
    return clinic_admin_out(clinic)


@router.get("/clinics/{clinic_id}/members", response_model=list[MemberOut])
async def members(clinic_id: UUID, _: Manager, session: Session) -> list[MemberOut]:
    return [MemberOut.model_validate(m) for m in await clinics.members(session, clinic_id)]


@router.post("/clinics/{clinic_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(clinic_id: UUID, body: MemberIn, admin: Manager, session: Session, ip: Ip) -> MemberOut:
    """`user_id` — auth-service foydalanuvchisi (admin `/v1/admin/users?phone=` orqali topadi)."""
    async with transaction(session):
        membership = await clinics.add_member(session, clinic_id, body.user_id, actor=Actor.admin(admin), ip=ip)
    return MemberOut.model_validate(membership)


@router.delete("/clinics/{clinic_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(clinic_id: UUID, user_id: UUID, admin: Manager, session: Session, ip: Ip) -> Response:
    async with transaction(session):
        await clinics.remove_member(session, clinic_id, user_id, actor=Actor.admin(admin), ip=ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/clinics/{clinic_id}/{action}", response_model=ClinicAdminOut)
async def clinic_status(
    clinic_id: UUID,
    action: Literal["publish", "suspend", "reinstate"],
    admin: Manager,
    session: Session,
    ip: Ip,
    body: StatusReason | None = None,
) -> ClinicAdminOut:
    """suspend — kelajakdagi bronlar 100% qaytarish bilan bekor qilinadi (booking, `clinic.suspended`)."""
    async with transaction(session):
        clinic = await clinics.set_status(
            session, clinic_id, action, actor=Actor.admin(admin), reason=body.reason if body else "", ip=ip
        )
    return clinic_admin_out(clinic)


# --- mutaxassisliklar ------------------------------------------------------------------------


@router.get("/specializations", response_model=list[SpecializationOut])
async def list_specializations(_: Manager, session: Session) -> list[SpecializationOut]:
    return [SpecializationOut.model_validate(s) for s in await specializations.list_all(session)]


@router.post("/specializations", response_model=SpecializationOut, status_code=status.HTTP_201_CREATED)
async def create_specialization(body: SpecializationIn, admin: Manager, session: Session, ip: Ip) -> SpecializationOut:
    async with transaction(session):
        spec = await specializations.create(session, body.model_dump(), actor=Actor.admin(admin), ip=ip)
    return SpecializationOut.model_validate(spec)


@router.patch("/specializations/{spec_id}", response_model=SpecializationOut)
async def update_specialization(
    spec_id: UUID, body: SpecializationUpdate, admin: Manager, session: Session, ip: Ip
) -> SpecializationOut:
    async with transaction(session):
        spec = await specializations.update(
            session, spec_id, body.model_dump(exclude_unset=True), actor=Actor.admin(admin), ip=ip
        )
    return SpecializationOut.model_validate(spec)
