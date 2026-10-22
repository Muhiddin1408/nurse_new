"""/v1/admin/users — admin JWT + `users.manage`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from medbron_common.audit import Actor
from medbron_common.context import client_ip
from medbron_common.db import transaction
from medbron_common.phone import normalize_phone

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.models import User
from app.repositories import identity as repo
from app.routers.deps import Session
from app.schemas.auth import AdminUserList, AdminUserOut, BlockRequest
from app.services import users

router = APIRouter(prefix="/v1/admin/users", tags=["admin"])

Admin = Annotated[CurrentAdmin, Depends(require_permission(permissions.USERS_MANAGE))]


def _out(user: User) -> AdminUserOut:
    return AdminUserOut.model_validate(user, from_attributes=True)


@router.get("", response_model=AdminUserList)
async def list_users(
    _: Admin,
    session: Session,
    phone: str | None = None,
    user_id: UUID | None = None,
    is_active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminUserList:
    normalized = normalize_phone(phone) if phone else None
    rows, total = await repo.list_users(
        session, phone=normalized, user_id=user_id, is_active=is_active, limit=limit, offset=offset
    )
    return AdminUserList(items=[_out(u) for u in rows], total=total, limit=limit, offset=offset)


@router.post("/{user_id}/block", response_model=AdminUserOut)
async def block_user(
    user_id: UUID, body: BlockRequest, admin: Admin, session: Session, request: Request
) -> AdminUserOut:
    async with transaction(session):
        user = await users.block(session, user_id, actor=Actor.admin(admin), reason=body.reason, ip=client_ip(request))
    return _out(user)


@router.post("/{user_id}/unblock", response_model=AdminUserOut)
async def unblock_user(user_id: UUID, admin: Admin, session: Session, request: Request) -> AdminUserOut:
    async with transaction(session):
        user = await users.unblock(session, user_id, actor=Actor.admin(admin), ip=client_ip(request))
    return _out(user)
