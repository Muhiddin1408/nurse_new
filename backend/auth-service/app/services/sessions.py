"""Token juftligi: access (RS256, 15 daq) + refresh (opaque, 30 kun, rotatsiya + qayta ishlatishni aniqlash)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tokens import AccessClaims, get_signer, sign_access
from app.metrics import REFRESH_REUSE, TOKENS_ISSUED
from app.models import RefreshToken, User
from app.repositories import identity as repo
from app.services.errors import AccountBlocked, RefreshInvalid, RefreshReused, RoleNotAvailable
from app.services.roles import RoleSet, compute_roles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientInfo:
    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class TokenPair:
    access: str
    refresh: str
    expires_in: int
    user_id: uuid.UUID
    active_role: str
    roles: list[str]
    doctor_id: uuid.UUID | None


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_pair(
    session: AsyncSession,
    user: User,
    role_set: RoleSet,
    active_role: str,
    *,
    client: ClientInfo,
    grant: str,
    family_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> tuple[TokenPair, RefreshToken]:
    """Chaqiruvchining tranzaksiyasi ichida: refresh qatori qo'shiladi, access imzolanadi."""
    if active_role not in role_set.roles:
        raise RoleNotAvailable()
    if user.phone is None:
        raise AccountBlocked()
    settings = get_settings()
    issued = now or datetime.now(UTC)
    raw_refresh = secrets.token_urlsafe(32)
    row = RefreshToken(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=hash_refresh(raw_refresh),
        active_role=active_role,
        issued_at=issued,
        expires_at=issued + timedelta(days=settings.refresh_token_ttl_days),
        ip_address=client.ip,
        user_agent=(client.user_agent or "")[:255] or None,
    )
    session.add(row)
    access = sign_access(
        get_signer(),
        AccessClaims(
            user_id=user.id,
            role=active_role,
            roles=role_set.roles,
            phone=user.phone,
            doctor_id=role_set.doctor_id,
        ),
        now=issued,
    )
    TOKENS_ISSUED.labels(grant=grant).inc()
    pair = TokenPair(
        access=access,
        refresh=raw_refresh,
        expires_in=settings.access_token_ttl_seconds,
        user_id=user.id,
        active_role=active_role,
        roles=role_set.roles,
        doctor_id=role_set.doctor_id,
    )
    return pair, row


async def _load_valid(session: AsyncSession, raw_refresh: str, now: datetime) -> RefreshToken:
    token = await repo.get_refresh_by_hash(session, hash_refresh(raw_refresh))
    if token is None:
        raise RefreshInvalid()
    if token.revoked_at is not None:
        if token.revoked_reason == "rotated":
            # Rotatsiya qilingan token qayta keldi: kimdir eski nusxani ushlab qolgan.
            # Butun oila bekor — haqiqiy egasi ham qayta kiradi, o'g'ri esa sessiyasiz qoladi.
            await repo.revoke_family(session, token.family_id, "reuse_detected", now)
            REFRESH_REUSE.inc()
            logger.warning("refresh token reuse detected", extra={"user_id": str(token.user_id)})
            raise RefreshReused()
        raise RefreshInvalid()
    if token.expires_at <= now:
        raise RefreshInvalid()
    return token


async def rotate(
    session: AsyncSession,
    raw_refresh: str,
    *,
    client: ClientInfo,
    new_role: str | None = None,
    expected_user: uuid.UUID | None = None,
) -> TokenPair:
    """Refresh rotatsiyasi (va `switch-role`). Tranzaksiya — chaqiruvchida.

    Diqqat: `RefreshReused` ko'tarilganda oila bekor qilingani COMMIT qilinishi kerak —
    router buni `commit_on=(RefreshReused,)` bilan ta'minlaydi."""
    now = datetime.now(UTC)
    token = await _load_valid(session, raw_refresh, now)
    if expected_user is not None and token.user_id != expected_user:
        raise RefreshInvalid()
    user = await repo.get_user(session, token.user_id, for_update=True)
    if user is None or not user.is_active:
        await repo.revoke_family(session, token.family_id, "deactivated", now)
        raise AccountBlocked()
    role_set = await compute_roles(session, user.id)
    if new_role is not None:
        if new_role not in role_set.roles:
            raise RoleNotAvailable()
        active = new_role
        user.preferred_role = new_role
    else:
        # Rol olib qo'yilgan bo'lsa (masalan, klinikadan chiqarildi) — mijoz roliga qaytadi.
        active = role_set.default_active(token.active_role)
    pair, _ = await issue_pair(
        session,
        user,
        role_set,
        active,
        client=client,
        grant="switch_role" if new_role else "refresh",
        family_id=token.family_id,
        now=now,
    )
    token.revoked_at = now
    token.revoked_reason = "rotated"
    return pair


async def logout(session: AsyncSession, raw_refresh: str, user_id: uuid.UUID) -> None:
    """Joriy qurilmadan chiqish. Begona token berilsa ham javob bir xil (mavjudligi oshkor qilinmaydi)."""
    now = datetime.now(UTC)
    token = await repo.get_refresh_by_hash(session, hash_refresh(raw_refresh))
    if token is None or token.user_id != user_id:
        raise RefreshInvalid()
    await repo.revoke_family(session, token.family_id, "logout", now)


async def logout_all(session: AsyncSession, user_id: uuid.UUID, reason: str = "logout_all") -> int:
    """Barcha qurilmalardan chiqish. Access tokenlar o'z muddati (15 daq) ichida eskiradi."""
    return await repo.revoke_all_for_user(session, user_id, reason, datetime.now(UTC))
