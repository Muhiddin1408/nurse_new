"""/v1/auth — telefon + OTP kirish, token yangilash, chiqish, rol almashtirish, JWKS."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from medbron_common.db import transaction

from app.core.security import CurrentUser, get_current_user
from app.core.tokens import get_signer
from app.repositories import identity as repo
from app.routers.deps import Client, Limiter, Session, Sms
from app.schemas.auth import (
    AuthUser,
    LoginOut,
    Me,
    OtpRequest,
    OtpRequested,
    OtpVerify,
    RefreshRequest,
    SwitchRoleRequest,
    TokenPairOut,
)
from app.services import otp, sessions
from app.services.errors import AccountBlocked, RefreshReused, UserNotFound
from app.services.roles import compute_roles

router = APIRouter(prefix="/v1/auth", tags=["auth"])

AuthedUser = Annotated[CurrentUser, Depends(get_current_user)]


def _pair_out(pair: sessions.TokenPair) -> TokenPairOut:
    return TokenPairOut(access=pair.access, refresh=pair.refresh, expires_in=pair.expires_in)


@router.post("/otp/request", response_model=OtpRequested, response_model_exclude_none=True)
async def request_otp(body: OtpRequest, session: Session, limiter: Limiter, sms: Sms, client: Client) -> OtpRequested:
    """SMS kod. Javob raqam tizimda bor-yo'qligidan qat'i nazar bir xil."""
    sent = await otp.request_otp(session, limiter, sms, body.phone, ip=client.ip, language=body.language)
    return OtpRequested(expires_in=sent.expires_in, retry_after=sent.retry_after, debug_code=sent.debug_code)


@router.post("/otp/verify", response_model=LoginOut)
async def verify_otp(body: OtpVerify, session: Session, limiter: Limiter, client: Client) -> LoginOut:
    """Kodni tekshirish → token juftligi. Raqam yangi bo'lsa user shu yerda yaratiladi (`is_new_user`)."""
    result = await otp.verify_otp(session, limiter, body.phone, body.code, client=client)
    pair = result.pair
    return LoginOut(
        access=pair.access,
        refresh=pair.refresh,
        expires_in=pair.expires_in,
        is_new_user=result.is_new_user,
        tokens={"access": pair.access, "refresh": pair.refresh},
        user=AuthUser(
            id=pair.user_id,
            phone=result.phone,
            roles=pair.roles,
            available_roles=pair.roles,
            active_role=pair.active_role,
            role=pair.active_role,
            doctor_id=pair.doctor_id,
        ),
    )


@router.post("/token/refresh", response_model=TokenPairOut)
async def refresh(body: RefreshRequest, session: Session, client: Client) -> TokenPairOut:
    """Rotatsiya: eski refresh bekor, yangi juftlik. Eski refresh qayta kelsa — butun sessiya yopiladi."""
    async with transaction(session, commit_on=(RefreshReused, AccountBlocked)):
        pair = await sessions.rotate(session, body.refresh, client=client)
    return _pair_out(pair)


@router.post("/switch-role", response_model=TokenPairOut)
async def switch_role(body: SwitchRoleRequest, user: AuthedUser, session: Session, client: Client) -> TokenPairOut:
    async with transaction(session, commit_on=(RefreshReused, AccountBlocked)):
        pair = await sessions.rotate(
            session, body.refresh, client=client, new_role=body.role, expected_user=user.user_id
        )
    return _pair_out(pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, user: AuthedUser, session: Session) -> Response:
    async with transaction(session):
        await sessions.logout(session, body.refresh, user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout/all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: AuthedUser, session: Session) -> Response:
    """Telefon yo'qolganda: barcha refresh tokenlar darhol bekor (access ≤ 15 daqiqada eskiradi)."""
    async with transaction(session):
        await sessions.logout_all(session, user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=Me)
async def me(user: AuthedUser, session: Session) -> Me:
    row = await repo.get_user(session, user.user_id)
    if row is None or not row.is_active:
        raise UserNotFound()
    role_set = await compute_roles(session, row.id)
    return Me(
        id=row.id,
        phone=row.phone,
        roles=role_set.roles,
        active_role=user.role if user.role in role_set.roles else role_set.default_active(row.preferred_role),
        doctor_id=role_set.doctor_id,
        created_at=row.created_at,
    )


@router.get("/.well-known/jwks.json")
async def jwks(response: Response) -> dict[str, Any]:
    """Public kalitlar (RFC 7517). Servislar kalitni fayldan o'qiydi; bu — tashqi/legacy tekshiruvchilar uchun."""
    response.headers["Cache-Control"] = "public, max-age=300"
    return get_signer().jwks()
