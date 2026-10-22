"""/v1/admin/files — admin JWT + `files.read` (maxfiy hujjatlar; har ochilish auditga)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from medbron_common.audit import Actor

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.repositories import files as repo
from app.routers.deps import Ip, Session, StorageDep, file_out
from app.schemas.files import FileOut, SignedUrlOut
from app.services import files
from app.services.errors import FileNotFound

router = APIRouter(prefix="/v1/admin/files", tags=["admin"])

Reader = Annotated[CurrentAdmin, Depends(require_permission(permissions.FILES_READ))]


@router.get("/{file_id}", response_model=FileOut)
async def get_file(file_id: UUID, _: Reader, session: Session, storage: StorageDep) -> FileOut:
    file = await repo.get(session, file_id)
    if file is None:
        raise FileNotFound()
    return file_out(storage, file)


@router.get("/{file_id}/url", response_model=SignedUrlOut)
async def file_url(file_id: UUID, admin: Reader, session: Session, storage: StorageDep, ip: Ip) -> SignedUrlOut:
    signed = await files.admin_url(session, storage, file_id, Actor.admin(admin), ip=ip)
    return SignedUrlOut(url=signed.url, expires_in=signed.expires_in)
