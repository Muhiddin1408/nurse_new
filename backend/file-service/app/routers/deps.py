from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip

from app.core.database import get_session
from app.core.storage import Storage, get_storage
from app.models import File
from app.schemas.files import FileOut
from app.services.files import urls_of

Session = Annotated[AsyncSession, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]


def get_ip(request: Request) -> str | None:
    return client_ip(request)


Ip = Annotated[str | None, Depends(get_ip)]


def file_out(storage: Storage, file: File) -> FileOut:
    url, thumb = urls_of(storage, file)
    return FileOut(
        id=file.id,
        purpose=file.purpose,
        visibility=file.visibility,
        status=file.status,
        content_type=file.content_type,
        size=file.size,
        original_name=file.original_name,
        url=url,
        thumbnail_url=thumb,
        created_at=file.created_at,
    )
