"""catalog eventlaridan rollar proyeksiyasi (ARCHITECTURE 9.1). Rollar to'plami o'zgarsa —
`user.roles_changed` (notification, analytics). Yangi rollar foydalanuvchining keyingi token
yangilanishida (≤ 15 daq) kuchga kiradi."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import PermanentError
from medbron_common.events import Events

from app.events import outbox
from app.repositories import identity as repo
from app.services.roles import compute_roles


class ProjectionNotReady(RuntimeError):
    """Kerakli oldingi event hali qo'llanmagan (masalan, `suspended` `approved` dan oldin keldi) —
    VAQTINCHALIK xato: consumer retry qiladi, tartib o'z-o'zidan tiklanadi."""


async def _ensure_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    if await repo.get_user(session, user_id) is None:
        # Shifokor/klinika a'zosi auth'dan o'tmagan user bo'la olmaydi — bu ma'lumot nomuvofiqligi,
        # qayta urinish yordam bermaydi: DLQ ga (ko'rinadigan bo'lsin).
        raise PermanentError(f"user {user_id} auth-service'da yo'q")


async def _publish_if_changed(session: AsyncSession, user_id: uuid.UUID, before: list[str]) -> None:
    after = (await compute_roles(session, user_id)).roles
    if set(after) == set(before):
        return
    outbox.add_event(
        session,
        Events.USER_ROLES_CHANGED,
        aggregate_id=user_id,
        data={
            "user_id": user_id,
            "roles": after,
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
        },
    )


async def doctor_status(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    doctor_id: uuid.UUID,
    status: str,
    occurred_at: datetime,
    requires_existing: bool,
) -> None:
    await _ensure_user(session, user_id)
    if requires_existing and await repo.get_doctor_link(session, user_id) is None:
        raise ProjectionNotReady(f"doctor {doctor_id}: approved hali qo'llanmagan")
    before = (await compute_roles(session, user_id)).roles
    await repo.upsert_doctor_link(session, user_id=user_id, doctor_id=doctor_id, status=status, changed_at=occurred_at)
    await _publish_if_changed(session, user_id, before)


async def clinic_membership(
    session: AsyncSession, *, user_id: uuid.UUID, clinic_id: uuid.UUID, active: bool, occurred_at: datetime
) -> None:
    await _ensure_user(session, user_id)
    before = (await compute_roles(session, user_id)).roles
    await repo.set_membership(session, user_id=user_id, clinic_id=clinic_id, active=active, changed_at=occurred_at)
    await _publish_if_changed(session, user_id, before)
