"""/v1/files — user JWT: presigned yuklash, complete, havola, o'chirish."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from medbron_common.audit import Actor
from medbron_common.db import transaction

from app.core.security import CurrentUser, get_current_user
from app.routers.deps import Ip, Session, StorageDep, file_out
from app.schemas.files import FileOut, SignedUrlOut, UploadOut, UploadRequest
from app.services import files

router = APIRouter(prefix="/v1/files", tags=["files"])

User = Annotated[CurrentUser, Depends(get_current_user)]


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def create_upload(body: UploadRequest, user: User, session: Session, storage: StorageDep) -> UploadOut:
    """Presigned PUT. Mijoz faylni `upload_url` ga `headers` bilan yuboradi, keyin `/complete`."""
    async with transaction(session):
        ticket = await files.create_upload(
            session,
            storage,
            user_id=user.user_id,
            purpose_name=body.purpose,
            content_type=body.content_type,
            size=body.size,
            filename=body.filename,
        )
    return UploadOut(
        file_id=ticket.file.id,
        upload_url=ticket.upload_url,
        headers={"Content-Type": ticket.file.content_type},
        expires_in=ticket.expires_in,
    )


@router.post("/{file_id}/complete", response_model=FileOut)
async def complete(file_id: UUID, user: User, session: Session, storage: StorageDep) -> FileOut:
    """Hajm va haqiqiy turni tekshiradi; ommaviy rasm tozalanadi (EXIF/GPS) va thumbnail yasaladi."""
    return file_out(storage, await files.complete(session, storage, file_id, user.user_id))


@router.get("/{file_id}", response_model=FileOut)
async def get_file(file_id: UUID, user: User, session: Session, storage: StorageDep) -> FileOut:
    return file_out(storage, await files.get_owned(session, file_id, user.user_id))


@router.get("/{file_id}/url", response_model=SignedUrlOut)
async def file_url(file_id: UUID, user: User, session: Session, storage: StorageDep, ip: Ip) -> SignedUrlOut:
    """Ommaviy fayl — doimiy havola; maxfiy — ≤ 5 daqiqalik presigned URL (har ochilish auditga)."""
    signed = await files.owner_url(session, storage, file_id, Actor.user(user), ip=ip)
    return SignedUrlOut(url=signed.url, expires_in=signed.expires_in)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(file_id: UUID, user: User, session: Session, storage: StorageDep) -> Response:
    async with transaction(session):
        await files.delete_own(session, storage, file_id, user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
