"""Klinikalar: hayot sikli (admin), a'zolar (clinic_admin), klinika profili va xonalar (klinika admini).

Hayot sikli (monolitda kod yo'q edi, ADR-0011): draft → active (publish) ↔ suspended (suspend / reinstate).
Klinika admini faqat moderatsiyasiz maydonlarni o'zgartiradi (tavsif, telefon, ish vaqti, rasm); nom, manzil
va koordinatalar — admin orqali.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.events import Events

from app.clients.file import FileClient, FileRejected
from app.events import outbox
from app.models import Clinic, ClinicMembership, Room
from app.repositories import catalog as repo
from app.services.common import clinic_payload, emit_clinic_updated
from app.services.errors import (
    AlreadyExists,
    ClinicNotFound,
    InvalidFile,
    InvalidRequest,
    InvalidTransition,
    NotClinicAdmin,
    RoomNotFound,
)

logger = logging.getLogger(__name__)

ADMIN_EDITABLE = ("name", "description", "phone", "city", "street", "latitude", "longitude", "working_hours")
CLINIC_ADMIN_EDITABLE = ("description", "phone", "working_hours")


async def require_managed(
    session: AsyncSession, user_id: uuid.UUID, clinic_id: uuid.UUID, *, for_update: bool = False
) -> Clinic:
    """Faol a'zolik DB'dan tekshiriladi. Boshqarmaydigan klinika — xuddi yo'qdek (404)."""
    if clinic_id not in await repo.managed_clinic_ids(session, user_id):
        raise NotClinicAdmin()
    clinic = await repo.get_clinic(session, clinic_id, for_update=for_update)
    if clinic is None:
        raise NotClinicAdmin()
    return clinic


# --- admin: hayot sikli ----------------------------------------------------------------------


async def create(session: AsyncSession, data: dict[str, Any], *, actor: Actor, ip: str | None) -> Clinic:
    clinic = Clinic(id=uuid.uuid4(), status="draft", **data)
    session.add(clinic)
    await session.flush()
    record_audit(
        outbox,
        session,
        actor,
        action="clinic.create",
        object_type="clinic",
        object_id=clinic.id,
        after={"name": clinic.name, "city": clinic.city},
        ip=ip,
    )
    return clinic


async def admin_update(
    session: AsyncSession, clinic_id: uuid.UUID, changes: dict[str, Any], *, actor: Actor, ip: str | None
) -> Clinic:
    clinic = await repo.get_clinic(session, clinic_id, for_update=True)
    if clinic is None:
        raise ClinicNotFound()
    before = {k: str(getattr(clinic, k)) for k in changes if k in ADMIN_EDITABLE}
    for key in ADMIN_EDITABLE:
        if key in changes:
            setattr(clinic, key, changes[key])
    if clinic.status != "draft":
        emit_clinic_updated(session, clinic)
    record_audit(
        outbox,
        session,
        actor,
        action="clinic.update",
        object_type="clinic",
        object_id=clinic.id,
        before=before,
        after={k: str(getattr(clinic, k)) for k in before},
        ip=ip,
    )
    return clinic


async def set_status(
    session: AsyncSession,
    clinic_id: uuid.UUID,
    action: str,
    *,
    actor: Actor,
    reason: str = "",
    ip: str | None,
) -> Clinic:
    clinic = await repo.get_clinic(session, clinic_id, for_update=True)
    if clinic is None:
        raise ClinicNotFound()
    allowed = {"publish": ("draft",), "suspend": ("active",), "reinstate": ("suspended",)}[action]
    target = "suspended" if action == "suspend" else "active"
    if clinic.status == target:
        return clinic
    if clinic.status not in allowed:
        raise InvalidTransition(extra={"status": clinic.status})
    if action == "suspend" and not reason.strip():
        raise InvalidRequest("Sabab majburiy")
    before = clinic.status
    clinic.status, clinic.status_reason = target, reason
    if action == "suspend":
        assert actor.actor_id is not None
        outbox.add_event(
            session,
            Events.CLINIC_SUSPENDED,
            aggregate_id=clinic.id,
            data={"clinic_id": clinic.id, "reason": reason, "suspended_by": actor.actor_id},
        )
    else:
        # publish va reinstate — ikkalasi ham "klinika ochiq" (schedule/search proyeksiyasi bir xil)
        outbox.add_event(session, Events.CLINIC_PUBLISHED, aggregate_id=clinic.id, data=clinic_payload(clinic))
    record_audit(
        outbox,
        session,
        actor,
        action=f"clinic.{action}",
        object_type="clinic",
        object_id=clinic.id,
        before={"status": before},
        after={"status": target, "reason": reason},
        ip=ip,
    )
    return clinic


# --- admin: a'zolar --------------------------------------------------------------------------


async def _remaining(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(ClinicMembership.user_id == user_id, ClinicMembership.is_active.is_(True))
    return int((await session.execute(stmt)).scalar_one())


async def add_member(
    session: AsyncSession, clinic_id: uuid.UUID, user_id: uuid.UUID, *, actor: Actor, ip: str | None
) -> ClinicMembership:
    if await repo.get_clinic(session, clinic_id) is None:
        raise ClinicNotFound()
    membership = await repo.get_membership(session, user_id, clinic_id)
    if membership is not None and membership.is_active:
        return membership
    if membership is None:
        membership = ClinicMembership(id=uuid.uuid4(), user_id=user_id, clinic_id=clinic_id, is_active=True)
        session.add(membership)
    else:
        membership.is_active = True
    await session.flush()
    outbox.add_event(
        session, Events.CLINIC_MEMBER_ADDED, aggregate_id=clinic_id, data={"clinic_id": clinic_id, "user_id": user_id}
    )
    record_audit(
        outbox,
        session,
        actor,
        action="clinic.member.add",
        object_type="clinic",
        object_id=clinic_id,
        after={"user_id": str(user_id)},
        ip=ip,
    )
    return membership


async def remove_member(
    session: AsyncSession, clinic_id: uuid.UUID, user_id: uuid.UUID, *, actor: Actor, ip: str | None
) -> None:
    membership = await repo.get_membership(session, user_id, clinic_id)
    if membership is None or not membership.is_active:
        return
    membership.is_active = False
    await session.flush()
    outbox.add_event(
        session,
        Events.CLINIC_MEMBER_REMOVED,
        aggregate_id=clinic_id,
        data={"clinic_id": clinic_id, "user_id": user_id, "remaining_clinics": await _remaining(session, user_id)},
    )
    record_audit(
        outbox,
        session,
        actor,
        action="clinic.member.remove",
        object_type="clinic",
        object_id=clinic_id,
        before={"user_id": str(user_id)},
        ip=ip,
    )


async def members(session: AsyncSession, clinic_id: uuid.UUID) -> list[ClinicMembership]:
    stmt = select(ClinicMembership).where(ClinicMembership.clinic_id == clinic_id, ClinicMembership.is_active.is_(True))
    return list((await session.scalars(stmt.order_by(ClinicMembership.created_at))).all())


# --- klinika admini --------------------------------------------------------------------------


async def update_profile(
    session: AsyncSession,
    files: FileClient,
    user_id: uuid.UUID,
    clinic_id: uuid.UUID,
    changes: dict[str, Any],
    *,
    ip: str | None,
) -> Clinic:
    clinic = await require_managed(session, user_id, clinic_id, for_update=True)
    before = {k: str(getattr(clinic, k)) for k in (*CLINIC_ADMIN_EDITABLE, "photo_file_id") if k in changes}
    if "photo_file_id" in changes:
        await _set_photo(files, clinic, user_id, changes.pop("photo_file_id"))
    for key in CLINIC_ADMIN_EDITABLE:
        if key in changes:
            setattr(clinic, key, changes[key])
    if clinic.status != "draft":
        emit_clinic_updated(session, clinic)
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "clinic_admin"),
        action="clinic.update",
        object_type="clinic",
        object_id=clinic.id,
        before=before,
        after={k: str(getattr(clinic, k, "")) for k in before},
        ip=ip,
    )
    return clinic


async def _set_photo(files: FileClient, clinic: Clinic, user_id: uuid.UUID, file_id: uuid.UUID | None) -> None:
    ref = f"clinic:{clinic.id}:photo"
    old = clinic.photo_file_id
    if file_id == old:
        return
    if file_id is None:
        clinic.photo_file_id, clinic.photo_url = None, None
    else:
        try:
            attached = await files.attach(file_id, owner_user_id=user_id, owner_ref=ref, purpose="clinic_photo")
        except FileRejected as exc:
            raise InvalidFile(extra={"reason": exc.code}) from exc
        clinic.photo_file_id, clinic.photo_url = attached.id, attached.url
    if old is not None:
        try:
            await files.release(old, ref)
        except Exception:
            logger.warning("old clinic photo release failed", extra={"file_id": str(old)})


async def rooms(session: AsyncSession, user_id: uuid.UUID, clinic_id: uuid.UUID) -> list[Room]:
    await require_managed(session, user_id, clinic_id)
    stmt = select(Room).where(Room.clinic_id == clinic_id).order_by(Room.name)
    return list((await session.scalars(stmt)).all())


async def create_room(
    session: AsyncSession, user_id: uuid.UUID, clinic_id: uuid.UUID, data: dict[str, Any], *, ip: str | None
) -> Room:
    await require_managed(session, user_id, clinic_id)
    room = Room(id=uuid.uuid4(), clinic_id=clinic_id, **data)
    session.add(room)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AlreadyExists("Bunday nomli xona bor") from exc
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "clinic_admin"),
        action="clinic.room.create",
        object_type="room",
        object_id=room.id,
        after={"name": room.name},
        ip=ip,
    )
    return room


async def update_room(
    session: AsyncSession,
    user_id: uuid.UUID,
    clinic_id: uuid.UUID,
    room_id: uuid.UUID,
    changes: dict[str, Any],
    *,
    ip: str | None,
) -> Room:
    await require_managed(session, user_id, clinic_id)
    room = await repo.get_room(session, room_id)
    if room is None or room.clinic_id != clinic_id:
        raise RoomNotFound()
    for key, value in changes.items():
        setattr(room, key, value)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AlreadyExists("Bunday nomli xona bor") from exc
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "clinic_admin"),
        action="clinic.room.update",
        object_type="room",
        object_id=room.id,
        after={k: str(v) for k, v in changes.items()},
        ip=ip,
    )
    return room
