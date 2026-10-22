"""Audit jurnali: `audit.recorded` (barcha servislardan) → `audit_log`."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope

from app.metrics import AUDIT_RECORDED
from app.repositories import admin as repo


async def record_from_event(session: AsyncSession, event: Envelope) -> bool:
    data = event.data
    inserted = await repo.insert_audit(
        session,
        {
            "event_id": event.event_id,
            "occurred_at": event.occurred_at,
            "producer": event.producer,
            "actor_id": uuid.UUID(data["actor_id"]) if data.get("actor_id") else None,
            "actor_type": data["actor_type"],
            "actor_role": data["actor_role"],
            "action": data["action"],
            "object_type": data["object_type"],
            "object_id": data["object_id"],
            "before": data.get("before"),
            "after": data.get("after"),
            "ip": data.get("ip"),
            "request_id": data.get("request_id"),
        },
    )
    if inserted:
        AUDIT_RECORDED.labels(producer=event.producer).inc()
    return inserted
