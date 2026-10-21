"""/v1/doctor — shifokor kabineti (user JWT). Shifokor `user_id` bo'yicha DB'dan topiladi va holati DB'dan
tekshiriladi: token'dagi rol/`doctor_id` faqat ishora (onboarding paytida `doctor_id` claim'i hali yo'q)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response, status

from medbron_common.db import transaction

from app.repositories import catalog as repo
from app.routers.convert import affiliation_out, doctor_private
from app.routers.deps import Files, Ip, Session, User
from app.schemas.catalog import (
    AffiliationOut,
    DoctorAffiliationOut,
    DoctorPrivate,
    DoctorProfileUpdate,
    DocumentIn,
    DocumentOut,
    LicenseUpdate,
    OnboardingProfile,
    OnboardingStatus,
    PriceHistoryOut,
    ServiceIn,
    ServiceOut,
    ServiceUpdate,
)
from app.services import affiliations, doctors, offerings
from app.services.errors import AffiliationNotFound

router = APIRouter(prefix="/v1/doctor", tags=["doctor"])


@router.post("/onboarding/start", response_model=DoctorPrivate)
async def start(user: User, session: Session, response: Response) -> DoctorPrivate:
    """Idempotent: profil yaratilsa 201, mavjud bo'lsa 200."""
    async with transaction(session):
        doctor, created = await doctors.start(session, user.user_id)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return await doctor_private(session, doctor)


@router.get("/onboarding/profile", response_model=DoctorPrivate)
async def onboarding_profile(user: User, session: Session) -> DoctorPrivate:
    return await doctor_private(session, await doctors.require_doctor(session, user.user_id))


@router.patch("/onboarding/profile", response_model=DoctorPrivate)
async def update_onboarding(body: OnboardingProfile, user: User, session: Session) -> DoctorPrivate:
    async with transaction(session):
        doctor = await doctors.update_onboarding(session, user.user_id, body.model_dump(exclude_unset=True))
    return await doctor_private(session, doctor)


@router.get("/onboarding/documents", response_model=list[DocumentOut])
async def list_documents(user: User, session: Session) -> list[DocumentOut]:
    doctor = await doctors.require_doctor(session, user.user_id)
    return [DocumentOut.model_validate(d) for d in await repo.documents(session, doctor.id)]


@router.post("/onboarding/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def add_document(body: DocumentIn, user: User, session: Session, files: Files) -> DocumentOut:
    """Fayl avval file-service'ga yuklanadi (`purpose=doctor_document`, `complete`), keyin shu yerga `file_id`."""
    async with transaction(session):
        document = await doctors.add_document(session, files, user.user_id, body.kind, body.file_id)
    return DocumentOut.model_validate(document)


@router.delete("/onboarding/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_document(document_id: UUID, user: User, session: Session, files: Files) -> Response:
    async with transaction(session):
        await doctors.remove_document(session, files, user.user_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/onboarding/submit", response_model=OnboardingStatus)
async def submit(user: User, session: Session) -> OnboardingStatus:
    async with transaction(session):
        doctor = await doctors.submit(session, user.user_id)
    return OnboardingStatus(
        status=doctor.status,
        reason=doctor.status_reason,
        missing=[],
        submitted_at=doctor.submitted_at,
        sla_due_at=doctors.sla_due_at(doctor),
    )


@router.get("/onboarding/status", response_model=OnboardingStatus)
async def onboarding_status(user: User, session: Session) -> OnboardingStatus:
    doctor = await doctors.require_doctor(session, user.user_id)
    missing = await doctors.missing_requirements(session, doctor) if doctor.status in doctors.EDITABLE else []
    return OnboardingStatus(
        status=doctor.status,
        reason=doctor.status_reason,
        missing=missing,
        submitted_at=doctor.submitted_at,
        sla_due_at=doctors.sla_due_at(doctor),
    )


@router.get("/profile", response_model=DoctorPrivate)
async def profile(user: User, session: Session) -> DoctorPrivate:
    return await doctor_private(session, await doctors.require_doctor(session, user.user_id))


@router.patch("/profile", response_model=DoctorPrivate)
async def update_profile(body: DoctorProfileUpdate, user: User, session: Session, files: Files) -> DoctorPrivate:
    """Tasdiqlangan shifokor — moderatsiyasiz maydonlar (to'lov rejimi, follow-up chegirma, uy chaqiruvi bazasi...)."""
    async with transaction(session):
        doctor = await doctors.update_profile(session, files, user.user_id, body.model_dump(exclude_unset=True))
    return await doctor_private(session, doctor)


@router.put("/license", response_model=DoctorPrivate)
async def update_license(body: LicenseUpdate, user: User, session: Session, ip: Ip) -> DoctorPrivate:
    """Litsenziyani yangilash — to'xtatilgan shifokor ham (ADR-0011 #8); qayta faollashtirish — admin."""
    async with transaction(session):
        doctor = await doctors.update_license(
            session, user.user_id, body.license_number, body.license_expires_at, ip=ip
        )
    return await doctor_private(session, doctor)


# --- xizmatlar -------------------------------------------------------------------------------


@router.get("/services", response_model=list[ServiceOut])
async def list_services(user: User, session: Session) -> list[ServiceOut]:
    return [ServiceOut.model_validate(s) for s in await offerings.doctor_services(session, user.user_id)]


@router.post("/services", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(body: ServiceIn, user: User, session: Session, ip: Ip) -> ServiceOut:
    async with transaction(session):
        service = await offerings.create(session, body.model_dump(), user_id=user.user_id, ip=ip)
    return ServiceOut.model_validate(service)


@router.patch("/services/{service_id}", response_model=ServiceOut)
async def update_service(service_id: UUID, body: ServiceUpdate, user: User, session: Session, ip: Ip) -> ServiceOut:
    changes = body.model_dump(exclude_unset=True, exclude={"confirm_large_change"})
    async with transaction(session):
        service = await offerings.update(
            session, service_id, changes, user_id=user.user_id, confirm_large_change=body.confirm_large_change, ip=ip
        )
    return ServiceOut.model_validate(service)


@router.delete("/services/{service_id}", response_model=ServiceOut)
async def deactivate_service(service_id: UUID, user: User, session: Session, ip: Ip) -> ServiceOut:
    """Har doim yumshoq (`is_active=false`) — qayta yoqish: PATCH `is_active: true`."""
    async with transaction(session):
        service = await offerings.deactivate(session, service_id, user_id=user.user_id, ip=ip)
    return ServiceOut.model_validate(service)


@router.get("/services/{service_id}/price-history", response_model=list[PriceHistoryOut])
async def price_history(service_id: UUID, user: User, session: Session) -> list[PriceHistoryOut]:
    return [PriceHistoryOut.model_validate(h) for h in await offerings.price_history(session, user.user_id, service_id)]


# --- klinika takliflari va hamkorliklar -------------------------------------------------------


@router.get("/clinic-invites", response_model=list[DoctorAffiliationOut])
async def invites(user: User, session: Session) -> list[DoctorAffiliationOut]:
    rows = await affiliations.doctor_affiliations(session, user.user_id, ("invited",))
    return [
        DoctorAffiliationOut(**affiliation_out(a).model_dump(), clinic_name=c.name, city=c.city, street=c.street)
        for a, c in rows
    ]


@router.post("/clinic-invites/{affiliation_id}/{action}", response_model=AffiliationOut)
async def respond(affiliation_id: UUID, action: str, user: User, session: Session, ip: Ip) -> AffiliationOut:
    if action not in ("accept", "decline"):
        raise AffiliationNotFound()
    async with transaction(session):
        aff = await affiliations.respond(session, user.user_id, affiliation_id, action == "accept", ip=ip)
    return affiliation_out(aff)


@router.get("/affiliations", response_model=list[DoctorAffiliationOut])
async def my_affiliations(user: User, session: Session) -> list[DoctorAffiliationOut]:
    rows = await affiliations.doctor_affiliations(session, user.user_id, ("active", "paused"))
    return [
        DoctorAffiliationOut(**affiliation_out(a).model_dump(), clinic_name=c.name, city=c.city, street=c.street)
        for a, c in rows
    ]


@router.post("/affiliations/{affiliation_id}/leave", response_model=AffiliationOut)
async def leave(affiliation_id: UUID, user: User, session: Session, ip: Ip) -> AffiliationOut:
    async with transaction(session):
        aff = await affiliations.leave(session, user.user_id, affiliation_id, ip=ip)
    return affiliation_out(aff)
