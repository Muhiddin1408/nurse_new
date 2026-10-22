"""Akkaunt holati: admin bloklashi/ochishi va o'chirish so'rovi (user-service eventidan)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.events import Events

from app.events import outbox
from app.models import User
from app.repositories import identity as repo
from app.services import sessions
from app.services.errors import InvalidStateTransition, UserNotFound

BLOCKED = "blocked_by_admin"
DELETED = "deletion_requested"


async def block(session: AsyncSession, user_id: uuid.UUID, *, actor: Actor, reason: str, ip: str | None) -> User:
    user = await repo.get_user(session, user_id, for_update=True)
    if user is None:
        raise UserNotFound()
    if not user.is_active:
        raise InvalidStateTransition("Akkaunt allaqachon faol emas")
    now = datetime.now(UTC)
    user.is_active = False
    user.deactivated_reason = BLOCKED
    user.deactivated_at = now
    revoked = await sessions.logout_all(session, user.id, reason="deactivated")
    outbox.add_event(
        session, Events.USER_DEACTIVATED, aggregate_id=user.id, data={"user_id": user.id, "reason": BLOCKED}
    )
    record_audit(
        outbox,
        session,
        actor,
        action="user.block",
        object_type="user",
        object_id=user.id,
        before={"is_active": True},
        after={"is_active": False, "reason": reason, "revoked_sessions": revoked},
        ip=ip,
    )
    return user


async def unblock(session: AsyncSession, user_id: uuid.UUID, *, actor: Actor, ip: str | None) -> User:
    user = await repo.get_user(session, user_id, for_update=True)
    if user is None:
        raise UserNotFound()
    if user.is_active or user.deactivated_reason != BLOCKED:
        # O'chirilgan akkaunt qayta tiklanmaydi — shaxsiy ma'lumotlar allaqachon anonimlashtirilgan.
        raise InvalidStateTransition("Faqat admin bloklagan akkauntni ochish mumkin")
    user.is_active = True
    user.deactivated_reason = None
    user.deactivated_at = None
    assert actor.actor_id is not None
    outbox.add_event(
        session,
        Events.USER_REACTIVATED,
        aggregate_id=user.id,
        data={"user_id": user.id, "reason": "unblocked_by_admin", "reactivated_by": actor.actor_id},
    )
    record_audit(
        outbox,
        session,
        actor,
        action="user.unblock",
        object_type="user",
        object_id=user.id,
        before={"is_active": False},
        after={"is_active": True},
        ip=ip,
    )
    return user


async def deactivate_for_deletion(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """`user.deletion_requested`: raqam bo'shatiladi (keyin shu raqam = yangi akkaunt), sessiyalar yopiladi."""
    user = await repo.get_user(session, user_id, for_update=True)
    if user is None:
        return False
    if user.deactivated_reason == DELETED and user.phone is None:
        return False
    user.phone = None
    user.is_active = False
    user.deactivated_reason = DELETED
    user.deactivated_at = datetime.now(UTC)
    await sessions.logout_all(session, user.id, reason="deactivated")
    outbox.add_event(
        session, Events.USER_DEACTIVATED, aggregate_id=user.id, data={"user_id": user.id, "reason": DELETED}
    )
    return True
