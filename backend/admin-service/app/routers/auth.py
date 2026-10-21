"""/v1/admin/auth — parol → TOTP → admin JWT. Bu ikki endpoint tokensiz (qolgan hammasi admin JWT)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from medbron_common.db import transaction

from app.core.security import CurrentAdmin, get_current_admin
from app.repositories import admin as repo
from app.routers.deps import Guard, Ip, Session
from app.schemas.admin import (
    AdminDetail,
    AdminOut,
    AdminSessionOut,
    LoginRequest,
    MfaChallengeOut,
    PasswordChange,
    TotpRequest,
)
from app.services import admins, login
from app.services.errors import AdminNotFound

router = APIRouter(prefix="/v1/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=MfaChallengeOut)
async def password_step(body: LoginRequest, session: Session, guard: Guard, ip: Ip) -> MfaChallengeOut:
    challenge = await login.password_step(session, guard, body.login, body.password, ip=ip)
    return MfaChallengeOut(mfa_token=challenge.mfa_token, expires_in=challenge.expires_in)


@router.post("/totp", response_model=AdminSessionOut)
async def totp_step(body: TotpRequest, session: Session, guard: Guard, ip: Ip) -> AdminSessionOut:
    result = await login.totp_step(session, guard, body.mfa_token, body.code, ip=ip)
    return AdminSessionOut(
        access=result.access,
        expires_in=result.expires_in,
        admin=AdminOut.model_validate(result.admin),
        permissions=result.permissions,
    )


@router.get("/me", response_model=AdminDetail)
async def me(admin: Annotated[CurrentAdmin, Depends(get_current_admin)], session: Session) -> AdminDetail:
    row = await repo.get_admin(session, admin.admin_id)
    if row is None or not row.is_active:
        raise AdminNotFound()
    return AdminDetail(
        **AdminOut.model_validate(row).model_dump(),
        role_ids=await repo.role_ids_of(session, row.id),
        permissions=await repo.permissions_of(session, row.id),
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange, admin: Annotated[CurrentAdmin, Depends(get_current_admin)], session: Session, ip: Ip
) -> Response:
    async with transaction(session):
        await admins.change_password(
            session, admin.admin_id, current=body.current_password, new=body.new_password, ip=ip
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
