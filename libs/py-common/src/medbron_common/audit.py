"""Imtiyozli amallar jurnali: `audit.recorded` eventi (yagona consumer — admin-service).

Audit yozuvi biznes o'zgarishi bilan BITTA tranzaksiyada outbox'ga tushadi — "amal bajarildi,
izi qolmadi" bo'lmaydi:

    async with session.begin():
        user.is_active = False
        record_audit(outbox, session, Actor.admin(admin), action="user.block",
                     object_type="user", object_id=user.id, before={...}, after={...}, ip=ip)

`before` / `after` ga faqat o'zgargan maydonlar yoziladi; telefon, karta, parol, TOTP siri — HECH QACHON.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from medbron_common.context import get_request_id
from medbron_common.events import Events

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from medbron_common.eventbus.outbox import Outbox
    from medbron_common.security import CurrentAdmin, CurrentUser


@dataclass(frozen=True)
class Actor:
    actor_type: str  # user | admin | system
    actor_id: uuid.UUID | None
    actor_role: str

    @classmethod
    def admin(cls, admin: CurrentAdmin) -> Actor:
        return cls("admin", admin.admin_id, "admin")

    @classmethod
    def user(cls, user: CurrentUser) -> Actor:
        return cls("user", user.user_id, user.role)

    @classmethod
    def system(cls, role: str = "system") -> Actor:
        return cls("system", None, role)


def record_audit(
    outbox: Outbox,
    session: AsyncSession,
    actor: Actor,
    *,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID,
    before: dict[str, Any] | list[Any] | None = None,
    after: dict[str, Any] | list[Any] | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    return outbox.add_event(
        session,
        Events.AUDIT_RECORDED,
        aggregate_id=str(object_id),
        data={
            "actor_id": str(actor.actor_id) if actor.actor_id else None,
            "actor_type": actor.actor_type,
            "actor_role": actor.actor_role,
            "action": action,
            "object_type": object_type,
            "object_id": str(object_id),
            "before": before,
            "after": after,
            "ip": ip,
            "request_id": get_request_id(),
        },
    )
