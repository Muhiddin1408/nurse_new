from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip

from app.clients.file import FileClient
from app.core.database import get_session

Session = Annotated[AsyncSession, Depends(get_session)]


def get_files(request: Request) -> FileClient:
    files: FileClient = request.app.state.file_client
    return files


def get_ip(request: Request) -> str | None:
    return client_ip(request)


Files = Annotated[FileClient, Depends(get_files)]
Ip = Annotated[str | None, Depends(get_ip)]
