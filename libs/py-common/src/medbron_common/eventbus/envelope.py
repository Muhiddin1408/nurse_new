"""Yagona event konverti (BLUEPRINT 8.3, `contracts/events/_envelope.v1.json`)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict


class Envelope(BaseModel):
    """Consumer tomonida o'qilgan event. Noma'lum maydonlarga bardoshli (`extra="ignore"`)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    event_id: uuid.UUID
    event_type: str
    event_version: int
    occurred_at: AwareDatetime
    producer: str
    trace_id: str | None = None
    aggregate_type: str
    aggregate_id: str
    data: dict[str, Any]


def _uuid7() -> uuid.UUID:
    """UUIDv7 (RFC 9562): vaqt bo'yicha tartiblanadi — indeks va debug uchun qulay."""
    if hasattr(uuid, "uuid7"):  # Python 3.14+
        return uuid.uuid7()  # type: ignore[no-any-return]  # pragma: no cover
    ms = int(datetime.now(UTC).timestamp() * 1000)
    rand = int.from_bytes(uuid.uuid4().bytes[6:], "big")  # 80 bit tasodif
    value = (ms & ((1 << 48) - 1)) << 80 | rand
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # variant RFC 4122
    return uuid.UUID(int=value)


new_event_id = _uuid7


def build_envelope(
    *,
    event_type: str,
    producer: str,
    aggregate_type: str,
    aggregate_id: str | uuid.UUID,
    data: dict[str, Any],
    event_version: int = 1,
    trace_id: str | None = None,
    event_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """JSON-ga tayyor dict (UUID/datetime string). Outbox `payload` ustuniga aynan shu yoziladi."""
    when = (occurred_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "event_id": str(event_id or new_event_id()),
        "event_type": event_type,
        "event_version": event_version,
        "occurred_at": when.isoformat().replace("+00:00", "Z"),
        "producer": producer,
        "trace_id": trace_id,
        "aggregate_type": aggregate_type,
        "aggregate_id": str(aggregate_id),
        "data": _jsonable(data),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        raise ValueError("Decimal event ichida taqiqlangan — pul *_tiyin butun son (ADR-0007)")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime event ichida taqiqlangan — UTC bilan bering")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value
