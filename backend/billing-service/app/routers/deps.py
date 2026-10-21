from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip

from app.core.database import get_session
from app.services.deps import Deps

Session = Annotated[AsyncSession, Depends(get_session)]


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


def get_ip(request: Request) -> str | None:
    return client_ip(request)


DepsDep = Annotated[Deps, Depends(get_deps)]
Ip = Annotated[str | None, Depends(get_ip)]
