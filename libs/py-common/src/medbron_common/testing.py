"""Servis testlari uchun yordamchilar: RSA kalit juftligi, token chiqarish, event sxema tekshiruvi.

Faqat testlarda ishlatiladi; prod kodi bu modulni import qilmaydi.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from medbron_common.events import CATALOG, event_schema, validate_event
from medbron_common.security import key_id


@dataclass(frozen=True)
class KeyPair:
    private_pem: bytes
    public_pem: bytes
    kid: str


def generate_keypair(bits: int = 2048) -> KeyPair:
    private = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return KeyPair(private_pem, public_pem, key_id(private.public_key()))


def mint_user_token(
    keys: KeyPair,
    *,
    user_id: uuid.UUID | None = None,
    roles: Iterable[str] = ("client",),
    doctor_id: uuid.UUID | None = None,
    phone: str = "+998901234567",
    ttl: timedelta = timedelta(minutes=15),
    issuer: str = "medbron-auth",
    audience: str = "medbron",
    token_type: str = "access",  # noqa: S107
    extra: dict[str, Any] | None = None,
) -> str:
    roles = list(roles)
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id or uuid.uuid4()),
        "role": roles[0],
        "roles": roles,
        "phone": phone,
        "iss": issuer,
        "aud": audience,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    if doctor_id:
        claims["doctor_id"] = str(doctor_id)
    claims.update(extra or {})
    return jwt.encode(claims, keys.private_pem, algorithm="RS256", headers={"kid": keys.kid})


def mint_admin_token(
    keys: KeyPair,
    *,
    admin_id: uuid.UUID | None = None,
    permissions: Iterable[str] = (),
    ttl: timedelta = timedelta(minutes=30),
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(admin_id or uuid.uuid4()),
        "permissions": list(permissions),
        "iss": "medbron-admin",
        "aud": "admin",
        "type": "access",
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, keys.private_pem, algorithm="RS256", headers={"kid": keys.kid})


def assert_event_valid(envelope: dict[str, Any]) -> None:
    """Publisher testlarida: payload kontraktga mos (BLUEPRINT 8.6 — "payload JSON Schema'ga mosmi")."""
    validate_event(envelope)


def _sample(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    if "$ref" in schema:
        return _sample(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = "null" if "null" in kind else kind[0]
    if kind == "null":
        return None
    if kind == "object":
        props = schema.get("properties", {})
        return {name: _sample(props[name], defs) for name in schema.get("required", [])}
    if kind == "array":
        return []
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    fmt = schema.get("format")
    if fmt == "uuid":
        return str(uuid.uuid4())
    if fmt == "date-time":
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")
    if fmt == "date":
        return datetime.now(UTC).date().isoformat()
    return "x" * int(schema.get("minLength", 1))


def sample_data(event_type: str, **overrides: Any) -> dict[str, Any]:
    """Kontraktga mos MINIMAL `data` (faqat majburiy maydonlar) + `overrides`. Natija sxemaga qarshi
    tekshiriladi — test o'zi noto'g'ri payload yasasa darhol bilinadi. Katalog o'zgarsa testlar o'zgarmaydi.

        data = sample_data(Events.BOOKING_CONFIRMED, client_id=str(uid), start_at="2026-10-25T10:00:00Z")
    """
    schema = event_schema(event_type)
    data_schema = schema["properties"]["data"]
    data = _sample(data_schema, schema.get("$defs", {}))
    data.update(overrides)
    spec = CATALOG[event_type]
    validate_event(
        {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "event_version": spec.version,
            "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "producer": spec.publishers[0],
            "trace_id": None,
            "aggregate_type": spec.aggregate,
            "aggregate_id": "x",
            "data": data,
        }
    )
    return dict(data)
