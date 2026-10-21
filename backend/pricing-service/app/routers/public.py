"""/v1/pricing — user JWT: narxni oldindan ko'rish, promo tekshirish, paketlar; /v1/doctor/packages — shifokor."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from medbron_common.db import transaction
from medbron_common.errors import Forbidden

from app.core.security import CurrentUser, get_current_user, get_optional_user
from app.models import Package
from app.repositories import pricing as repo
from app.routers.deps import DepsDep, Session
from app.schemas.pricing import (
    EnrollmentOut,
    PackageIn,
    PackageOut,
    PackageUpdate,
    PreviewOut,
    PreviewRequest,
    PromoValidateOut,
    PromoValidateRequest,
)
from app.services import packages, quotes
from app.services.errors import PromoInvalidError

router = APIRouter(tags=["pricing"])

User = Annotated[CurrentUser, Depends(get_current_user)]
OptionalUser = Annotated[CurrentUser | None, Depends(get_optional_user)]


def _request(user: CurrentUser, body: PreviewRequest) -> quotes.QuoteRequest:
    return quotes.QuoteRequest(
        client_id=user.user_id,
        doctor_id=body.doctor_id,
        service_ids=list(body.service_ids),
        slot_start_at=None,
        price_adjust_percent=body.price_adjust_percent,
        place=body.place,
        promo_code=body.promo_code,
    )


@router.post("/v1/pricing/quote", response_model=PreviewOut)
async def preview(body: PreviewRequest, user: User, session: Session, deps: DepsDep) -> PreviewOut:
    """Hech narsa band qilinmaydi. Yakuniy narx bron yaratilganda (schedule'dagi haqiqiy slot foizi bilan)."""
    quote = await quotes.preview(session, deps, _request(user, body))
    return PreviewOut.model_validate(quote)


@router.post("/v1/pricing/promo/validate", response_model=PromoValidateOut)
async def validate_promo(body: PromoValidateRequest, user: User, session: Session, deps: DepsDep) -> PromoValidateOut:
    req = PreviewRequest(
        doctor_id=body.doctor_id,
        service_ids=body.service_ids,
        promo_code=body.code,
        price_adjust_percent=body.price_adjust_percent,
    )
    try:
        quote = await quotes.preview(session, deps, _request(user, req))
    except PromoInvalidError as exc:
        return PromoValidateOut(valid=False, reason=str(exc.extra["reason"]), message=exc.message, discount_tiyin=0)
    applied = quote["discount_kind"] == "promo"
    return PromoValidateOut(
        valid=True,
        # Promo to'g'ri, lekin boshqa (kattaroq) chegirma yutdi — chegirmalar qo'shilmaydi
        reason="" if applied else "other_discount_better",
        message="",
        discount_tiyin=quote["discount_tiyin"] if applied else 0,
    )


@router.get("/v1/pricing/packages", response_model=list[PackageOut])
async def doctor_packages(doctor_id: UUID, _: OptionalUser, session: Session, deps: DepsDep) -> list[PackageOut]:
    rows = await repo.list_packages(session, doctor_id=doctor_id, only_active=True)
    async with transaction(session):
        views = [await packages.offer_view(session, deps, p) for p in rows]
    return [PackageOut.model_validate(v) for v in views]


def _enrollment(view: packages.EnrollmentView) -> EnrollmentOut:
    e = view.enrollment
    return EnrollmentOut(
        id=e.id, package_id=e.package_id, doctor_id=e.doctor_id, service_id=e.service_id, package_name=e.package_name,
        discount_percent=e.discount_percent, sessions_total=e.sessions_total, sessions_left=view.sessions_left,
        expires_at=e.expires_at,
    )  # fmt: skip


@router.post(
    "/v1/pricing/packages/{package_id}/enroll", response_model=EnrollmentOut, status_code=status.HTTP_201_CREATED
)
async def enroll(package_id: UUID, user: User, session: Session, deps: DepsDep) -> EnrollmentOut:
    return _enrollment(await packages.enroll(session, deps, user.user_id, package_id))


@router.get("/v1/pricing/packages/mine", response_model=list[EnrollmentOut])
async def my_packages(user: User, session: Session) -> list[EnrollmentOut]:
    return [_enrollment(v) for v in await packages.mine(session, user.user_id)]


# --- shifokor ---------------------------------------------------------------------------------


async def require_doctor(user: User) -> UUID:
    """`doctor_id` JWT claim'i (auth-service: tasdiqlangan shifokor). Sessiyadan OLDIN tekshiriladi."""
    if user.doctor_id is None or not user.has_role("doctor"):
        raise Forbidden(code="ROLE_REQUIRED", message="Faqat tasdiqlangan shifokor")
    return user.doctor_id


DoctorId = Annotated[UUID, Depends(require_doctor)]


async def _out(session: Session, deps: DepsDep, package: Package) -> PackageOut:
    async with transaction(session):
        return PackageOut.model_validate(await packages.offer_view(session, deps, package))


@router.get("/v1/doctor/packages", response_model=list[PackageOut])
async def list_own(doctor_id: DoctorId, session: Session, deps: DepsDep) -> list[PackageOut]:
    rows = await repo.list_packages(session, doctor_id=doctor_id, only_active=False)
    return [await _out(session, deps, p) for p in rows]


@router.post("/v1/doctor/packages", response_model=PackageOut, status_code=status.HTTP_201_CREATED)
async def create_package(body: PackageIn, doctor_id: DoctorId, session: Session, deps: DepsDep) -> PackageOut:
    package = await packages.create_package(session, deps, doctor_id, body.model_dump())
    return await _out(session, deps, package)


@router.patch("/v1/doctor/packages/{package_id}", response_model=PackageOut)
async def update_package(
    package_id: UUID, body: PackageUpdate, doctor_id: DoctorId, session: Session, deps: DepsDep
) -> PackageOut:
    package = await packages.update_package(session, doctor_id, package_id, body.model_dump(exclude_unset=True))
    return await _out(session, deps, package)
