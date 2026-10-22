"""Stateless autentifikatsiya (BLUEPRINT 6).

- User JWT: auth-service RS256 bilan imzolaydi, `aud=medbron`.
- Admin JWT: admin-service RS256 (alohida kalit), `aud=admin`, `permissions[]`.
- Internal: `X-Internal-Token` + `X-Caller`; har chaqiruvchining o'z tokeni.

Har servis public key'ni fayldan o'qiydi — tarmoq chaqiruvi va DB lookup YO'Q.
Kalit rotatsiyasi: fayl bir nechta PEM blokini saqlashi mumkin; token header'idagi `kid`
bo'yicha tanlanadi (`kid = key_id(public_key)`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr

from medbron_common.errors import AppError, Forbidden, ServiceUnavailable, Unauthorized

if TYPE_CHECKING:
    from medbron_common.config import ServiceSettings

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
_PEM_BLOCK = re.compile(rb"-----BEGIN PUBLIC KEY-----.+?-----END PUBLIC KEY-----", re.S)
_LEEWAY_SECONDS = 10


class TokenInvalid(Unauthorized):
    code = "TOKEN_INVALID"
    message = "Token yaroqsiz"


class TokenExpired(Unauthorized):
    code = "TOKEN_EXPIRED"
    message = "Token muddati tugagan"


class WrongAudience(Forbidden):
    code = "WRONG_AUDIENCE"
    message = "Bu token ushbu endpoint uchun emas"


class PermissionDenied(Forbidden):
    code = "PERMISSION_DENIED"
    message = "Bu amal uchun ruxsat yo'q"


class InternalAuthFailed(Unauthorized):
    code = "INTERNAL_AUTH_FAILED"
    message = "Ichki so'rov autentifikatsiyasi muvaffaqiyatsiz"


def key_id(public_key: RSAPublicKey) -> str:
    """Kalitning barqaror identifikatori: SPKI DER ning SHA-256 si (base64url, 16 belgi)."""
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode()[:16]


def load_public_keys(pem: bytes) -> dict[str, RSAPublicKey]:
    blocks = _PEM_BLOCK.findall(pem)
    if not blocks:
        raise ValueError("PEM ichida PUBLIC KEY bloki topilmadi")
    keys: dict[str, RSAPublicKey] = {}
    for block in blocks:
        key = serialization.load_pem_public_key(block)
        if not isinstance(key, RSAPublicKey):
            raise ValueError("faqat RSA public key qo'llab-quvvatlanadi")
        keys[key_id(key)] = key
    return keys


class JWTVerifier:
    def __init__(
        self,
        keys: dict[str, RSAPublicKey],
        *,
        issuer: str,
        audience: str,
        expected_type: str = "access",
    ) -> None:
        if not keys:
            raise ValueError("kamida bitta public key kerak")
        self._keys = keys
        self.issuer = issuer
        self.audience = audience
        self.expected_type = expected_type

    @classmethod
    def from_pem(cls, pem: bytes, **kwargs: Any) -> JWTVerifier:
        return cls(load_public_keys(pem), **kwargs)

    def _select_key(self, token: str) -> RSAPublicKey:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenInvalid() from exc
        if header.get("alg") != ALGORITHM:
            # `alg=none` va HS256 (public key'ni HMAC siri sifatida) hujumlariga qarshi.
            raise TokenInvalid("Token algoritmi qabul qilinmaydi")
        kid = header.get("kid")
        if kid is not None:
            key = self._keys.get(kid)
            if key is None:
                raise TokenInvalid("Noma'lum imzo kaliti")
            return key
        if len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise TokenInvalid("Tokenda `kid` yo'q")

    def _check_audience_early(self, token: str) -> None:
        # Admin token user endpointiga (yoki aksincha) kelganda kalit ham boshqa bo'ladi —
        # imzo tekshiruvidan oldin aniq 403 WRONG_AUDIENCE beramiz (BLUEPRINT 6.2).
        # Imzosiz o'qilgan claim faqat RAD ETISH uchun ishlatiladi, hech qachon ruxsat uchun emas.
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise TokenInvalid() from exc
        aud = unverified.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if self.audience not in audiences:
            raise WrongAudience()

    def verify(self, token: str) -> dict[str, Any]:
        self._check_audience_early(token)
        key = self._select_key(token)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key,
                algorithms=[ALGORITHM],
                audience=self.audience,
                issuer=self.issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpired() from exc
        except jwt.InvalidAudienceError as exc:
            raise WrongAudience() from exc
        except jwt.PyJWTError as exc:
            raise TokenInvalid() from exc
        if claims.get("type") != self.expected_type:
            raise TokenInvalid("Token turi noto'g'ri")
        return claims


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: UUID
    role: str
    roles: frozenset[str]
    doctor_id: UUID | None = None
    phone: str | None = None
    token_id: str | None = None

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> CurrentUser:
        try:
            roles = frozenset(claims.get("roles") or [claims["role"]])
            doctor_id = claims.get("doctor_id")
            return cls(
                user_id=UUID(claims["sub"]),
                role=claims["role"],
                roles=roles,
                doctor_id=UUID(doctor_id) if doctor_id else None,
                phone=claims.get("phone"),
                token_id=claims.get("jti"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise TokenInvalid("Token claim'lari noto'g'ri") from exc


@dataclass(frozen=True, slots=True)
class CurrentAdmin:
    admin_id: UUID
    permissions: frozenset[str]
    token_id: str | None = None

    def can(self, permission: str) -> bool:
        return permission in self.permissions or "*" in self.permissions

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> CurrentAdmin:
        try:
            return cls(
                admin_id=UUID(claims["sub"]),
                permissions=frozenset(claims.get("permissions") or []),
                token_id=claims.get("jti"),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise TokenInvalid("Token claim'lari noto'g'ri") from exc


@dataclass
class SecurityConfig:
    """Servisning autentifikatsiya sozlamasi (`app.state.security`).

    Kalitlar birinchi ishlatilganda o'qiladi: lokal ishlab chiqishda kalitsiz ham servis
    ko'tariladi (faqat himoyalangan endpointlar 503 qaytaradi). Prod'da `load_eagerly()`.
    """

    user_key_path: Path | None = None
    admin_key_path: Path | None = None
    issuer: str = "medbron-auth"
    audience: str = "medbron"
    admin_issuer: str = "medbron-admin"
    admin_audience: str = "admin"
    inbound_tokens: dict[str, SecretStr] = field(default_factory=dict)
    _user_verifier: JWTVerifier | None = None
    _admin_verifier: JWTVerifier | None = None

    @classmethod
    def from_settings(cls, settings: ServiceSettings) -> SecurityConfig:
        return cls(
            user_key_path=settings.jwt_public_key_path,
            admin_key_path=settings.admin_jwt_public_key_path,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            admin_issuer=settings.admin_jwt_issuer,
            admin_audience=settings.admin_jwt_audience,
            inbound_tokens=settings.inbound_internal_tokens(),
        )

    @classmethod
    def for_tests(
        cls,
        *,
        user_public_pem: bytes | None = None,
        admin_public_pem: bytes | None = None,
        inbound_tokens: dict[str, str] | None = None,
    ) -> SecurityConfig:
        cfg = cls(inbound_tokens={k: SecretStr(v) for k, v in (inbound_tokens or {}).items()})
        if user_public_pem:
            cfg._user_verifier = JWTVerifier.from_pem(user_public_pem, issuer=cfg.issuer, audience=cfg.audience)
        if admin_public_pem:
            cfg._admin_verifier = JWTVerifier.from_pem(
                admin_public_pem, issuer=cfg.admin_issuer, audience=cfg.admin_audience
            )
        return cfg

    def _load(self, path: Path | None, issuer: str, audience: str, kind: str) -> JWTVerifier:
        if path is None or not path.is_file():
            logger.error("jwt public key missing", extra={"kind": kind, "path": str(path)})
            raise ServiceUnavailable(code="AUTH_KEYS_MISSING", message="Autentifikatsiya sozlanmagan")
        return JWTVerifier.from_pem(path.read_bytes(), issuer=issuer, audience=audience)

    @property
    def user_verifier(self) -> JWTVerifier:
        if self._user_verifier is None:
            self._user_verifier = self._load(self.user_key_path, self.issuer, self.audience, "user")
        return self._user_verifier

    @property
    def admin_verifier(self) -> JWTVerifier:
        if self._admin_verifier is None:
            self._admin_verifier = self._load(self.admin_key_path, self.admin_issuer, self.admin_audience, "admin")
        return self._admin_verifier

    def load_eagerly(self) -> None:
        _ = self.user_verifier
        _ = self.admin_verifier

    def check_internal(self, caller: str | None, token: str | None, allowed: Iterable[str]) -> str:
        if not caller or not token:
            raise InternalAuthFailed()
        expected = self.inbound_tokens.get(caller)
        # compare_digest ni token topilmaganda ham chaqiramiz — javob vaqti bo'yicha
        # "bunday chaqiruvchi bormi" ni bilib bo'lmasin.
        expected_value = expected.get_secret_value() if expected else ""
        valid = hmac.compare_digest(token.encode(), expected_value.encode())
        if not valid or expected is None:
            raise InternalAuthFailed()
        if caller not in set(allowed):
            raise Forbidden(code="CALLER_NOT_ALLOWED", message=f"{caller} bu endpointni chaqira olmaydi")
        return caller


_bearer = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def _security(request: Request) -> SecurityConfig:
    cfg = getattr(request.app.state, "security", None)
    if not isinstance(cfg, SecurityConfig):
        raise ServiceUnavailable(code="AUTH_NOT_CONFIGURED", message="Autentifikatsiya sozlanmagan")
    return cfg


def _token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise Unauthorized()
    return credentials.credentials


async def get_current_user(request: Request, credentials: BearerCredentials) -> CurrentUser:
    claims = _security(request).user_verifier.verify(_token(credentials))
    user = CurrentUser.from_claims(claims)
    request.state.user = user
    return user


async def get_optional_user(request: Request, credentials: BearerCredentials) -> CurrentUser | None:
    """Ochiq endpoint, lekin token kelsa — tekshiriladi (yaroqsiz token jim e'tiborsiz qoldirilmaydi)."""
    if credentials is None:
        return None
    return await get_current_user(request, credentials)


async def get_current_admin(request: Request, credentials: BearerCredentials) -> CurrentAdmin:
    claims = _security(request).admin_verifier.verify(_token(credentials))
    admin = CurrentAdmin.from_claims(claims)
    request.state.admin = admin
    return admin


def require_roles(*roles: str) -> Callable[..., Awaitable[CurrentUser]]:
    async def dependency(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if not user.has_role(*roles):
            raise Forbidden(code="ROLE_REQUIRED", message="Bu amal uchun rol yetarli emas")
        return user

    return dependency


def require_permission(permission: str) -> Callable[..., Awaitable[CurrentAdmin]]:
    async def dependency(admin: Annotated[CurrentAdmin, Depends(get_current_admin)]) -> CurrentAdmin:
        if not admin.can(permission):
            raise PermissionDenied(extra={"permission": permission})
        return admin

    return dependency


def verify_internal_token(*allowed_callers: str) -> Callable[..., Awaitable[str]]:
    """`/internal/v1/*` routerlari uchun: `dependencies=[Depends(verify_internal_token("booking-service"))]`."""
    if not allowed_callers:
        raise ValueError("kamida bitta ruxsat etilgan chaqiruvchi ko'rsating")

    async def dependency(
        request: Request,
        x_internal_token: Annotated[str | None, Header()] = None,
        x_caller: Annotated[str | None, Header()] = None,
    ) -> str:
        caller = _security(request).check_internal(x_caller, x_internal_token, allowed_callers)
        request.state.caller = caller
        return caller

    return dependency


__all__ = [
    "AppError",
    "CurrentAdmin",
    "CurrentUser",
    "InternalAuthFailed",
    "JWTVerifier",
    "PermissionDenied",
    "SecurityConfig",
    "TokenExpired",
    "TokenInvalid",
    "WrongAudience",
    "get_current_admin",
    "get_current_user",
    "get_optional_user",
    "key_id",
    "load_public_keys",
    "require_permission",
    "require_roles",
    "verify_internal_token",
]
