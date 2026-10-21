"""/internal/v1/file — faqat servislararo, `X-Internal-Token` bilan. Kong tashqariga OCHMAYDI.

Ruxsat etilgan chaqiruvchilar — docs/SERVICE_REGISTRY.md dagi ro'yxat bilan bir xil bo'lishi shart.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from medbron_common.db import transaction
from medbron_common.errors import Forbidden

from app.core.security import verify_internal_token
from app.routers.deps import Session, StorageDep, file_out
from app.schemas.files import AttachRequest, FileOut, ReleaseRequest, SignedUrlOut
from app.services import files

ALLOWED_CALLERS: tuple[str, ...] = (
    "catalog-service",
    "dispute-service",
    "user-service",
)


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(
    prefix="/internal/v1/files",
    tags=["internal"],
    # Xavfsiz standart: ro'yxat bo'sh bo'lsa — hech kim chaqira olmaydi.
    dependencies=[Depends(verify_internal_token(*ALLOWED_CALLERS) if ALLOWED_CALLERS else _deny_all)],
)


def _caller(request: Request) -> str:
    """Router darajasidagi `verify_internal_token` tasdiqlagan chaqiruvchi."""
    caller: str = request.state.caller
    return caller


Caller = Annotated[str, Depends(_caller)]


@router.post("/{file_id}/attach", response_model=FileOut)
async def attach(file_id: UUID, body: AttachRequest, caller: Caller, session: Session, storage: StorageDep) -> FileOut:
    """Egasi servis faylni o'z obyektiga biriktiradi (`owner_ref`, masalan `user:<id>:avatar`).
    Faqat `owner_user_id` yuklagan va `complete` dan o'tgan fayl; purpose shu servisga ruxsat etilgan bo'lishi shart."""
    async with transaction(session):
        file = await files.attach(
            session, file_id, caller=caller, owner_user_id=body.owner_user_id, owner_ref=body.owner_ref,
            purpose_name=body.purpose,
        )  # fmt: skip
    return file_out(storage, file)


@router.post("/{file_id}/release", status_code=status.HTTP_204_NO_CONTENT)
async def release(
    file_id: UUID, body: ReleaseRequest, caller: Caller, session: Session, storage: StorageDep
) -> Response:
    async with transaction(session):
        await files.release(session, storage, file_id, caller=caller, owner_ref=body.owner_ref)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{file_id}/url", response_model=SignedUrlOut)
async def file_url(
    file_id: UUID,
    caller: Caller,
    session: Session,
    storage: StorageDep,
    ttl: Annotated[int | None, Query(ge=30, le=300)] = None,
    viewer_type: Annotated[str | None, Query(max_length=16)] = None,
    viewer_id: Annotated[str | None, Query(max_length=64)] = None,
) -> SignedUrlOut:
    """Chaqiruvchi o'zi biriktirgan fayl uchun qisqa muddatli URL; `viewer_*` — kim ko'rayotgani (auditga)."""
    signed = await files.service_url(
        session, storage, file_id, caller=caller, ttl=ttl, viewer_type=viewer_type, viewer_id=viewer_id
    )
    return SignedUrlOut(url=signed.url, expires_in=signed.expires_in)
