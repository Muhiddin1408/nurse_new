from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip

from app.core.database import get_session
from app.services.login import LoginGuard

Session = Annotated[AsyncSession, Depends(get_session)]


def get_guard(request: Request) -> LoginGuard:
    guard: LoginGuard = request.app.state.login_guard
    return guard


def get_ip(request: Request) -> str | None:
    return client_ip(request)


Guard = Annotated[LoginGuard, Depends(get_guard)]
Ip = Annotated[str | None, Depends(get_ip)]
