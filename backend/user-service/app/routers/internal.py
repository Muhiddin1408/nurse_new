"""/internal/v1/user — faqat servislararo, `X-Internal-Token` bilan. Kong tashqariga OCHMAYDI.

Ruxsat etilgan chaqiruvchilar — docs/SERVICE_REGISTRY.md dagi ro'yxat bilan bir xil bo'lishi shart.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from medbron_common.errors import Forbidden

from app.core.security import verify_internal_token
from app.repositories import users as repo
from app.routers.deps import Session
from app.schemas.users import InternalAddress, InternalPatient
from app.services.errors import AddressNotFound, PatientNotFound

ALLOWED_CALLERS: tuple[str, ...] = ("booking-service",)


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(
    prefix="/internal/v1/users",
    tags=["internal"],
    # Xavfsiz standart: ro'yxat bo'sh bo'lsa — hech kim chaqira olmaydi.
    dependencies=[Depends(verify_internal_token(*ALLOWED_CALLERS) if ALLOWED_CALLERS else _deny_all)],
)


@router.get("/{user_id}/patients/{patient_id}", response_model=InternalPatient)
async def get_patient(user_id: UUID, patient_id: UUID, session: Session) -> InternalPatient:
    """Egalik tekshiruvi bilan: boshqasining bemori — 404 (booking bron yaratishda chaqiradi)."""
    patient = await repo.get_owned_patient(session, user_id, patient_id)
    if patient is None:
        raise PatientNotFound()
    return InternalPatient.model_validate(patient)


@router.get("/{user_id}/addresses/{address_id}", response_model=InternalAddress)
async def get_address(user_id: UUID, address_id: UUID, session: Session) -> InternalAddress:
    address = await repo.get_owned_address(session, user_id, address_id)
    if address is None:
        raise AddressNotFound()
    return InternalAddress.model_validate(address)
