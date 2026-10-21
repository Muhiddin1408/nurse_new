"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Admin, AdminRole, AuditLog, PlatformSetting, Role, SettingsVersion

# --- admins ----------------------------------------------------------------------------------


async def get_admin(session: AsyncSession, admin_id: uuid.UUID, *, for_update: bool = False) -> Admin | None:
    stmt = select(Admin).where(Admin.id == admin_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def get_admin_by_login(session: AsyncSession, login: str, *, for_update: bool = False) -> Admin | None:
    stmt = select(Admin).where(Admin.login == login)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def list_admins(session: AsyncSession) -> Sequence[Admin]:
    return (await session.scalars(select(Admin).order_by(Admin.created_at))).all()


async def role_ids_of(session: AsyncSession, admin_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await session.scalars(select(AdminRole.role_id).where(AdminRole.admin_id == admin_id))
    return list(rows.all())


async def permissions_of(session: AsyncSession, admin_id: uuid.UUID) -> list[str]:
    stmt = (
        select(func.unnest(Role.permissions))
        .join(AdminRole, AdminRole.role_id == Role.id)
        .where(AdminRole.admin_id == admin_id)
    )
    return sorted(set((await session.scalars(stmt)).all()))


async def set_admin_roles(session: AsyncSession, admin_id: uuid.UUID, role_ids: Sequence[uuid.UUID]) -> None:
    await session.execute(delete(AdminRole).where(AdminRole.admin_id == admin_id))
    if role_ids:
        await session.execute(insert(AdminRole).values([{"admin_id": admin_id, "role_id": r} for r in role_ids]))


async def count_active_admins_with(session: AsyncSession, permission: str) -> int:
    """`admins.manage` (yoki `*`) ga ega faol adminlar soni — oxirgisini o'chirib qo'yishdan himoya."""
    stmt = (
        select(func.count(func.distinct(Admin.id)))
        .join(AdminRole, AdminRole.admin_id == Admin.id)
        .join(Role, Role.id == AdminRole.role_id)
        .where(Admin.is_active.is_(True), Role.permissions.overlap([permission, "*"]))
    )
    return int((await session.execute(stmt)).scalar_one())


# --- roles -----------------------------------------------------------------------------------


async def get_role(session: AsyncSession, role_id: uuid.UUID) -> Role | None:
    return await session.get(Role, role_id)


async def get_roles(session: AsyncSession, role_ids: Sequence[uuid.UUID]) -> Sequence[Role]:
    if not role_ids:
        return []
    return (await session.scalars(select(Role).where(Role.id.in_(role_ids)))).all()


async def get_role_by_name(session: AsyncSession, name: str) -> Role | None:
    return (await session.scalars(select(Role).where(Role.name == name))).one_or_none()


async def list_roles(session: AsyncSession) -> Sequence[Role]:
    return (await session.scalars(select(Role).order_by(Role.name))).all()


async def role_in_use(session: AsyncSession, role_id: uuid.UUID) -> bool:
    stmt = select(AdminRole.admin_id).where(AdminRole.role_id == role_id).limit(1)
    return (await session.execute(stmt)).first() is not None


# --- audit -----------------------------------------------------------------------------------


async def insert_audit(session: AsyncSession, values: dict[str, Any]) -> bool:
    stmt = insert(AuditLog).values(**values).on_conflict_do_nothing(index_elements=[AuditLog.event_id])
    result = await session.execute(stmt.returning(AuditLog.id))
    return result.scalar_one_or_none() is not None


async def query_audit(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    object_type: str | None,
    object_id: str | None,
    action: str | None,
    producer: str | None,
    since: datetime | None,
    until: datetime | None,
    before_id: int | None,
    limit: int,
) -> Sequence[AuditLog]:
    """Keyset pagination (`before_id`) — jurnal millionlab qator bo'lganda ham OFFSET siz tez."""
    stmt = select(AuditLog)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if object_type:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditLog.object_id == object_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if producer:
        stmt = stmt.where(AuditLog.producer == producer)
    if since:
        stmt = stmt.where(AuditLog.occurred_at >= since)
    if until:
        stmt = stmt.where(AuditLog.occurred_at < until)
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    return (await session.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit))).all()


# --- settings --------------------------------------------------------------------------------


async def load_settings(session: AsyncSession) -> tuple[dict[str, Any], int]:
    rows = (await session.scalars(select(PlatformSetting))).all()
    version = (await session.execute(select(SettingsVersion.version).where(SettingsVersion.id == 1))).scalar()
    return {row.section: row.value for row in rows}, int(version or 0)


async def lock_settings_version(session: AsyncSession) -> int:
    """Versiya qatorini qulflaydi (parallel PUT'lar ketma-ket bajariladi)."""
    await session.execute(
        insert(SettingsVersion).values(id=1, version=0).on_conflict_do_nothing(index_elements=[SettingsVersion.id])
    )
    stmt = select(SettingsVersion.version).where(SettingsVersion.id == 1).with_for_update()
    return int((await session.execute(stmt)).scalar_one())


async def save_section(
    session: AsyncSession, section: str, value: dict[str, Any], admin_id: uuid.UUID, now: datetime
) -> None:
    stmt = insert(PlatformSetting).values(section=section, value=value, updated_at=now, updated_by=admin_id)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[PlatformSetting.section],
            set_={"value": value, "updated_at": now, "updated_by": admin_id},
        )
    )


async def bump_version(session: AsyncSession, now: datetime) -> int:
    stmt = (
        update(SettingsVersion)
        .where(SettingsVersion.id == 1)
        .values(version=SettingsVersion.version + 1, updated_at=now)
        .returning(SettingsVersion.version)
    )
    return int((await session.execute(stmt)).scalar_one())
