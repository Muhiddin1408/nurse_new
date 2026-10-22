"""
auth_bridge.py — strangler-fig ko'prigi (ARCHITECTURE 12, 2-bosqich).

Kirish auth-service'ga o'tgach, frontend/ilova RS256 access token (kid, `iss=medbron-auth`,
`aud=medbron`) oladi. Monolit hali bron, to'lov va katalogni o'zi bajaradi — shuning uchun u ham shu
tokenni qabul qilishi kerak. Bu klass:

  * faqat `alg=RS256` tokenlarni ko'radi (HS256 — eski simplejwt tokenlari — keyingi klassga o'tadi);
  * imzoni `MEDBRON_JWT_PUBLIC_KEY_FILE` dagi public kalit(lar) bilan tekshiradi (`kid` — rotatsiya);
  * `sub` — user UUID. Ma'lumot ko'chirish (scripts/migrate) UUID'larni saqlaydi, shuning uchun user
    odatda allaqachon bor. Ko'chirishdan keyin auth-service'da ro'yxatdan o'tgan yangi user birinchi
    so'rovda shu yerda yaratiladi (telefon token claim'idan). Shu telefon BOSHQA UUID bilan band bo'lsa —
    rad etiladi (ko'chirish bajarilmagan yoki ma'lumot nomuvofiq; jim qo'shib ketish xavfli).

Faqat sozlanganda yoqiladi (`MEDBRON_JWT_PUBLIC_KEY_FILE`). Monolit o'chirilganda bu fayl ham o'chadi.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from functools import lru_cache

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

logger = logging.getLogger(__name__)

ISSUER = "medbron-auth"
AUDIENCE = "medbron"
_PEM_BEGIN = b"-----BEGIN PUBLIC KEY-----"


def key_id(public_key: RSAPublicKey) -> str:
    """libs/py-common `key_id` bilan bir xil: SPKI DER ning SHA-256 si (base64url, 16 belgi)."""
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode()[:16]


@lru_cache(maxsize=1)
def _keys() -> dict[str, RSAPublicKey]:
    path = getattr(settings, "MEDBRON_JWT_PUBLIC_KEY_FILE", "")
    if not path:
        return {}
    with open(path, "rb") as fh:
        pem = fh.read()
    keys: dict[str, RSAPublicKey] = {}
    for block in pem.split(_PEM_BEGIN)[1:]:
        key = serialization.load_pem_public_key(_PEM_BEGIN + block)
        if isinstance(key, RSAPublicKey):
            keys[key_id(key)] = key
    return keys


class AuthServiceTokenAuthentication(BaseAuthentication):
    www_authenticate_realm = "api"

    def authenticate_header(self, request):
        return f'Bearer realm="{self.www_authenticate_realm}"'

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if len(header) != 2 or header[0].lower() != b"bearer":
            return None
        token = header[1].decode(errors="replace")
        try:
            unverified = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            return None
        if unverified.get("alg") != "RS256":
            return None  # eski HS256 token — simplejwt klassi tekshiradi
        keys = _keys()
        if not keys:
            return None
        key = keys.get(unverified.get("kid")) if unverified.get("kid") else next(iter(keys.values()))
        if key is None:
            raise exceptions.AuthenticationFailed("Noma'lum imzo kaliti")
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                leeway=10,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise exceptions.AuthenticationFailed("Token muddati tugagan") from exc
        except jwt.PyJWTError as exc:
            raise exceptions.AuthenticationFailed("Token yaroqsiz") from exc
        if claims.get("type") != "access":
            raise exceptions.AuthenticationFailed("Token turi noto'g'ri")

        user = self._user(claims)
        if not user.is_active:
            raise exceptions.AuthenticationFailed("Akkaunt bloklangan")
        # api/roles.get_active_role `active_role` claim'ini o'qiydi; auth-service uni `role` deb yozadi
        return user, {**claims, "active_role": claims.get("role"), "available_roles": claims.get("roles", [])}

    @staticmethod
    def _user(claims: dict):
        from apps.account.models import User

        try:
            user_id = uuid.UUID(str(claims["sub"]))
        except ValueError as exc:
            raise exceptions.AuthenticationFailed("Token claim'lari noto'g'ri") from exc
        user = User.objects.filter(id=user_id).first()
        if user is not None:
            return user
        phone = claims.get("phone")
        if not phone:
            raise exceptions.AuthenticationFailed("Token claim'lari noto'g'ri")
        if User.objects.filter(phone=phone).exists():
            logger.error("auth bridge: phone belongs to another user id", extra={"user_id": str(user_id)})
            raise exceptions.AuthenticationFailed("Akkaunt ma'lumotlari mos emas — qo'llab-quvvatlashga murojaat qiling")
        try:
            with transaction.atomic():
                user = User(id=user_id, phone=phone, is_phone_verified=True)
                user.set_unusable_password()
                user.save()
        except IntegrityError:
            user = User.objects.filter(id=user_id).first()
            if user is None:
                raise exceptions.AuthenticationFailed("Akkaunt yaratib bo'lmadi") from None
        logger.info("auth bridge: user provisioned", extra={"user_id": str(user_id)})
        return user
