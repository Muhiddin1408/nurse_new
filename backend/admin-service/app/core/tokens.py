"""Admin access token (RS256, `aud=admin`, `permissions[]`) — alohida kalit (ADR-0005)."""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache

from medbron_common.signing import TokenSigner, read_private_pem

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_signer() -> TokenSigner:
    settings = get_settings()
    public_path = settings.admin_jwt_public_key_path
    return TokenSigner.from_pem(
        read_private_pem(settings.admin_jwt_private_key, settings.admin_jwt_private_key_path),
        public_path.read_bytes() if public_path.is_file() else None,
        issuer=settings.admin_jwt_issuer,
        audience=settings.admin_jwt_audience,
        ttl=timedelta(seconds=settings.admin_access_token_ttl_seconds),
    )
