"""Admin kirishi: parol → TOTP → admin JWT (ARCHITECTURE 5.1, ADR-0005).

1. `POST /v1/admin/auth/login {login, password}` → `{mfa_token}` (5 daq, bir martalik, Redis).
2. `POST /v1/admin/auth/totp {mfa_token, code}` → `{access}` (`aud=admin`, `permissions[]`, 30 daq).

Himoya: login bo'yicha 5 xato → 15 daq blok; IP bo'yicha soatiga 30 urinish; noma'lum login uchun ham
argon2 tekshiruvi (vaqt bo'yicha login ro'yxatini taxmin qilib bo'lmaydi); TOTP replay — rad.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit

from app.core import crypto
from app.core.config import get_settings
from app.core.tokens import get_signer
from app.events import outbox
from app.metrics import ADMIN_LOGINS
from app.models import Admin
from app.repositories import admin as repo
from app.services.errors import InvalidCredentials, LimiterUnavailable, LoginLocked, MfaExpired, MfaInvalid

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MfaChallenge:
    mfa_token: str
    expires_in: int


@dataclass(frozen=True)
class AdminSession:
    access: str
    expires_in: int
    admin: Admin
    permissions: list[str]


def normalize_login(login: str) -> str:
    return login.strip().lower()


def _mfa_key(token: str) -> str:
    return f"admin:mfa:{hashlib.sha256(token.encode()).hexdigest()}"


class LoginGuard:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self, login: str, ip: str | None) -> None:
        settings = get_settings()
        try:
            failures = await self._redis.get(f"admin:login_fail:{login}")
            if failures is not None and int(failures) >= settings.login_max_failures:
                raise LoginLocked()
            if ip:
                pipe = self._redis.pipeline(transaction=True)
                pipe.incr(f"admin:login_ip:{ip}")
                pipe.expire(f"admin:login_ip:{ip}", 3600, nx=True)
                count, _ = await pipe.execute()
                if int(count) > settings.login_max_per_ip_hour:
                    raise LoginLocked()
        except RedisError as exc:
            raise LimiterUnavailable() from exc

    async def fail(self, login: str) -> None:
        settings = get_settings()
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(f"admin:login_fail:{login}")
            pipe.expire(f"admin:login_fail:{login}", settings.login_lockout_seconds, nx=True)
            await pipe.execute()
        except RedisError as exc:
            raise LimiterUnavailable() from exc

    async def succeed(self, login: str) -> None:
        try:
            await self._redis.delete(f"admin:login_fail:{login}")
        except RedisError:
            return

    async def issue_mfa(self, admin_id: uuid.UUID) -> MfaChallenge:
        settings = get_settings()
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"admin_id": str(admin_id), "attempts": 0})
        try:
            await self._redis.set(_mfa_key(token), payload, ex=settings.mfa_token_ttl_seconds)
        except RedisError as exc:
            raise LimiterUnavailable() from exc
        return MfaChallenge(mfa_token=token, expires_in=settings.mfa_token_ttl_seconds)

    async def load_mfa(self, token: str) -> uuid.UUID:
        try:
            raw = await self._redis.get(_mfa_key(token))
        except RedisError as exc:
            raise LimiterUnavailable() from exc
        if raw is None:
            raise MfaExpired()
        return uuid.UUID(json.loads(raw)["admin_id"])

    async def mfa_failed(self, token: str) -> None:
        """Bitta MFA token bilan 5 ta noto'g'ri kod — token yo'q qilinadi (parol qaytadan)."""
        key = _mfa_key(token)
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return
            data = json.loads(raw)
            data["attempts"] += 1
            if data["attempts"] >= get_settings().mfa_max_attempts:
                await self._redis.delete(key)
            else:
                await self._redis.set(key, json.dumps(data), keepttl=True)
        except RedisError as exc:
            raise LimiterUnavailable() from exc

    async def consume_mfa(self, token: str) -> None:
        try:
            await self._redis.delete(_mfa_key(token))
        except RedisError:
            return


async def password_step(
    session: AsyncSession, guard: LoginGuard, raw_login: str, password: str, *, ip: str | None
) -> MfaChallenge:
    login = normalize_login(raw_login)
    await guard.check(login, ip)
    admin = await repo.get_admin_by_login(session, login)
    if not crypto.verify_password(admin.password_hash if admin else None, password) or admin is None:
        await guard.fail(login)
        ADMIN_LOGINS.labels(result="bad_password").inc()
        raise InvalidCredentials()
    if not admin.is_active:
        ADMIN_LOGINS.labels(result="inactive").inc()
        raise InvalidCredentials()
    if crypto.needs_rehash(admin.password_hash):
        # Monolitdan ko'chirilgan pbkdf2 (yoki eski argon2 parametrlari) — birinchi kirishda yangilanadi.
        admin.password_hash = crypto.hash_password(password)
        await session.commit()
    return await guard.issue_mfa(admin.id)


async def totp_step(
    session: AsyncSession, guard: LoginGuard, mfa_token: str, code: str, *, ip: str | None
) -> AdminSession:
    admin_id = await guard.load_mfa(mfa_token)
    admin = await repo.get_admin(session, admin_id, for_update=True)
    if admin is None or not admin.is_active:
        await guard.consume_mfa(mfa_token)
        raise MfaExpired()
    step = crypto.match_totp_step(
        crypto.decrypt_secret(admin.totp_secret_enc), code, at=time.time(), last_step=admin.totp_last_step
    )
    if step is None:
        admin_login = admin.login  # rollback'dan keyin obyekt "expired" — atributni oldin olamiz
        await session.rollback()
        await guard.mfa_failed(mfa_token)
        await guard.fail(admin_login)
        ADMIN_LOGINS.labels(result="bad_totp").inc()
        raise MfaInvalid()

    now = datetime.now(UTC)
    admin.totp_last_step = step
    admin.last_login_at = now
    permissions = await repo.permissions_of(session, admin.id)
    record_audit(
        outbox,
        session,
        Actor("admin", admin.id, "admin"),
        action="admin.login",
        object_type="admin",
        object_id=admin.id,
        after={"permissions": permissions},
        ip=ip,
    )
    await session.commit()
    await guard.consume_mfa(mfa_token)
    await guard.succeed(admin.login)
    settings = get_settings()
    access = get_signer().sign(admin.id, {"permissions": permissions, "login": admin.login}, now=now)
    ADMIN_LOGINS.labels(result="ok").inc()
    logger.info("admin login", extra={"admin_id": str(admin.id)})
    return AdminSession(
        access=access, expires_in=settings.admin_access_token_ttl_seconds, admin=admin, permissions=permissions
    )
