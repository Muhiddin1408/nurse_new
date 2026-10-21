"""User access token (RS256, `kid`) — `medbron_common.signing.TokenSigner` orqali.

Imzo kaliti — `JWT_PRIVATE_KEY` / `JWT_PRIVATE_KEY_PATH` (faqat auth-service API konteyneriga). Tekshirish kalitlari —
`JWT_PUBLIC_KEY_PATH` (rotatsiyada bir nechta PEM bloki: eski + yangi). JWKS ikkalasini ham e'lon qiladi.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from medbron_common.signing import TokenSigner, read_private_pem

from app.core.config import get_settings


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    role: str
    roles: list[str]
    phone: str
    doctor_id: uuid.UUID | None


def sign_access(signer: TokenSigner, claims: AccessClaims, *, now: datetime | None = None) -> str:
    payload: dict[str, Any] = {"role": claims.role, "roles": claims.roles, "phone": claims.phone}
    if claims.doctor_id is not None:
        payload["doctor_id"] = str(claims.doctor_id)
    return signer.sign(claims.user_id, payload, now=now)


@lru_cache(maxsize=1)
def get_signer() -> TokenSigner:
    settings = get_settings()
    public_path = settings.jwt_public_key_path
    return TokenSigner.from_pem(
        read_private_pem(settings.jwt_private_key, settings.jwt_private_key_path),
        public_path.read_bytes() if public_path.is_file() else None,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        ttl=timedelta(seconds=settings.access_token_ttl_seconds),
    )
