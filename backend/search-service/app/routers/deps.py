from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip

from app.core.database import get_session

Session = Annotated[AsyncSession, Depends(get_session)]


def get_redis_dep(request: Request) -> Any:
    return getattr(request.app.state, "redis", None)


def get_ip(request: Request) -> str | None:
    return client_ip(request)


RedisDep = Annotated[Any, Depends(get_redis_dep)]
Ip = Annotated[str | None, Depends(get_ip)]
