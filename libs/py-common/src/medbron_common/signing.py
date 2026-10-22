"""RS256 token imzolash — faqat imzolovchi servislar (auth-service: user, admin-service: admin) uchun.

Boshqa servislar bu modulni ishlatmaydi: ular faqat public kalit bilan TEKSHIRADI (`security.py`).
`kid` = `key_id(public_key)` — tekshiruvchilar rotatsiyada kalitni shu bo'yicha tanlaydi.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from pydantic import SecretStr

from medbron_common.security import ALGORITHM, key_id, load_public_keys


def read_private_pem(inline: SecretStr | None, path: Path) -> bytes:
    """Imzo kaliti: muhit o'zgaruvchisidan (PEM yoki base64(PEM) — compose `secrets/private/<servis>.env`)
    yoki fayldan. O'zgaruvchi ustun: konteyner (uid 10001) host'dagi 0600 faylni o'qiy olmaydi, `env_file`
    esa compose tomonidan host foydalanuvchisi huquqi bilan o'qiladi."""
    if inline is not None and inline.get_secret_value().strip():
        raw = inline.get_secret_value().strip()
        return raw.encode() if raw.startswith("-----BEGIN") else base64.b64decode(raw)
    if not path.is_file():
        raise RuntimeError(f"JWT private key topilmadi: {path} (scripts/gen-jwt-keys.sh)")
    return path.read_bytes()


class TokenSigner:
    def __init__(
        self,
        private_key: RSAPrivateKey,
        *,
        issuer: str,
        audience: str,
        ttl: timedelta,
        public_keys: dict[str, RSAPublicKey] | None = None,
    ) -> None:
        self._key = private_key
        self.kid = key_id(private_key.public_key())
        self.issuer = issuer
        self.audience = audience
        self.ttl = ttl
        # JWKS: joriy imzo kaliti + rotatsiya davridagi eski public kalitlar
        self._public_keys = {self.kid: private_key.public_key(), **(public_keys or {})}

    @classmethod
    def from_pem(cls, private_pem: bytes, public_pem: bytes | None = None, **kwargs: Any) -> TokenSigner:
        key = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(key, RSAPrivateKey):
            raise ValueError("faqat RSA private key qo'llab-quvvatlanadi")
        return cls(key, public_keys=load_public_keys(public_pem) if public_pem else None, **kwargs)

    @classmethod
    def from_files(cls, private_path: Path, public_path: Path | None = None, **kwargs: Any) -> TokenSigner:
        if not private_path.is_file():
            raise RuntimeError(f"JWT private key topilmadi: {private_path} (scripts/gen-jwt-keys.sh)")
        public_pem = public_path.read_bytes() if public_path is not None and public_path.is_file() else None
        return cls.from_pem(private_path.read_bytes(), public_pem, **kwargs)

    def sign(self, subject: str | uuid.UUID, claims: dict[str, Any], *, now: datetime | None = None) -> str:
        """`claims` ga standart maydonlar (sub, iss, aud, type, iat, exp, jti) qo'shiladi."""
        issued = now or datetime.now(UTC)
        payload: dict[str, Any] = {
            **claims,
            "sub": str(subject),
            "iss": self.issuer,
            "aud": self.audience,
            "type": "access",
            "iat": issued,
            "exp": issued + self.ttl,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(payload, self._key, algorithm=ALGORITHM, headers={"kid": self.kid})

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [_jwk(kid, key) for kid, key in self._public_keys.items()]}


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(kid: str, key: RSAPublicKey) -> dict[str, str]:
    numbers = key.public_numbers()
    return {"kty": "RSA", "use": "sig", "alg": ALGORITHM, "kid": kid, "n": _b64(numbers.n), "e": _b64(numbers.e)}
