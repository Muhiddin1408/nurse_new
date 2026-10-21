"""Admin akkauntlari va rollari (`admins.manage`). Har o'zgarish — `audit.recorded`."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit

from app.core import crypto
from app.core.config import get_settings
from app.core.permissions import ADMINS_MANAGE, validate_permissions
from app.events import outbox
from app.models import Admin, Role
from app.repositories import admin as repo
from app.services.errors import (
    AdminNotFound,
    AlreadyExists,
    InvalidCredentials,
    LastAdminManager,
    RoleInUse,
    RoleNotFound,
    WeakPassword,
)
from app.services.login import normalize_login


@dataclass(frozen=True)
class Enrollment:
    """Yangi admin yoki TOTP qayta o'rnatish natijasi — `totp_uri` FAQAT shu javobda ko'rsatiladi."""

    admin: Admin
    totp_uri: str


def check_password(password: str) -> None:
    settings = get_settings()
    if len(password) < settings.password_min_length:
        raise WeakPassword(f"Parol kamida {settings.password_min_length} belgidan iborat bo'lishi kerak")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise WeakPassword("Parolda katta va kichik harf hamda raqam bo'lishi kerak")


async def _roles(session: AsyncSession, role_ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    found = await repo.get_roles(session, role_ids)
    if len(found) != len(set(role_ids)):
        raise RoleNotFound()
    return [r.id for r in found]


async def _ensure_manager_remains(session: AsyncSession) -> None:
    await session.flush()
    if await repo.count_active_admins_with(session, ADMINS_MANAGE) == 0:
        raise LastAdminManager()


async def create_admin(
    session: AsyncSession,
    *,
    login: str,
    full_name: str,
    password: str,
    role_ids: Sequence[uuid.UUID],
    actor: Actor,
    ip: str | None,
) -> Enrollment:
    check_password(password)
    normalized = normalize_login(login)
    roles = await _roles(session, role_ids)
    secret = crypto.new_totp_secret()
    admin = Admin(
        id=uuid.uuid4(),
        login=normalized,
        full_name=full_name,
        password_hash=crypto.hash_password(password),
        totp_secret_enc=crypto.encrypt_secret(secret),
    )
    session.add(admin)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AlreadyExists("Bunday login allaqachon mavjud") from exc
    await repo.set_admin_roles(session, admin.id, roles)
    record_audit(
        outbox,
        session,
        actor,
        action="admin.create",
        object_type="admin",
        object_id=admin.id,
        after={"login": normalized, "role_ids": [str(r) for r in roles]},
        ip=ip,
    )
    return Enrollment(admin=admin, totp_uri=crypto.provisioning_uri(secret, normalized))


async def update_admin(
    session: AsyncSession,
    admin_id: uuid.UUID,
    *,
    full_name: str | None,
    is_active: bool | None,
    role_ids: Sequence[uuid.UUID] | None,
    actor: Actor,
    ip: str | None,
) -> Admin:
    admin = await repo.get_admin(session, admin_id, for_update=True)
    if admin is None:
        raise AdminNotFound()
    before: dict[str, object] = {}
    after: dict[str, object] = {}
    if full_name is not None and full_name != admin.full_name:
        before["full_name"], after["full_name"] = admin.full_name, full_name
        admin.full_name = full_name
    if is_active is not None and is_active != admin.is_active:
        before["is_active"], after["is_active"] = admin.is_active, is_active
        admin.is_active = is_active
    if role_ids is not None:
        old = sorted(str(r) for r in await repo.role_ids_of(session, admin.id))
        new_roles = await _roles(session, role_ids)
        if sorted(str(r) for r in new_roles) != old:
            before["role_ids"], after["role_ids"] = old, sorted(str(r) for r in new_roles)
            await repo.set_admin_roles(session, admin.id, new_roles)
    if after:
        await _ensure_manager_remains(session)
        record_audit(
            outbox, session, actor, action="admin.update", object_type="admin", object_id=admin.id,
            before=before, after=after, ip=ip,
        )  # fmt: skip
    return admin


async def reset_totp(session: AsyncSession, admin_id: uuid.UUID, *, actor: Actor, ip: str | None) -> Enrollment:
    """Telefon yo'qolganda: yangi TOTP sir (eski kodlar darhol yaroqsiz)."""
    admin = await repo.get_admin(session, admin_id, for_update=True)
    if admin is None:
        raise AdminNotFound()
    secret = crypto.new_totp_secret()
    admin.totp_secret_enc = crypto.encrypt_secret(secret)
    admin.totp_last_step = None
    record_audit(outbox, session, actor, action="admin.reset_totp", object_type="admin", object_id=admin.id, ip=ip)
    return Enrollment(admin=admin, totp_uri=crypto.provisioning_uri(secret, admin.login))


async def change_password(
    session: AsyncSession, admin_id: uuid.UUID, *, current: str, new: str, ip: str | None
) -> None:
    admin = await repo.get_admin(session, admin_id, for_update=True)
    if admin is None or not admin.is_active:
        raise AdminNotFound()
    if not crypto.verify_password(admin.password_hash, current):
        raise InvalidCredentials("Joriy parol noto'g'ri")
    check_password(new)
    admin.password_hash = crypto.hash_password(new)
    admin.password_changed_at = datetime.now(UTC)
    record_audit(
        outbox, session, Actor("admin", admin.id, "admin"), action="admin.change_password", object_type="admin",
        object_id=admin.id, ip=ip,
    )  # fmt: skip


# --- roles -----------------------------------------------------------------------------------


async def create_role(
    session: AsyncSession, *, name: str, description: str, permissions: list[str], actor: Actor, ip: str | None
) -> Role:
    role = Role(id=uuid.uuid4(), name=name, description=description, permissions=validate_permissions(permissions))
    session.add(role)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AlreadyExists("Bunday nomli rol allaqachon mavjud") from exc
    record_audit(
        outbox, session, actor, action="role.create", object_type="admin_role", object_id=role.id,
        after={"name": name, "permissions": role.permissions}, ip=ip,
    )  # fmt: skip
    return role


async def update_role(
    session: AsyncSession,
    role_id: uuid.UUID,
    *,
    description: str | None,
    permissions: list[str] | None,
    actor: Actor,
    ip: str | None,
) -> Role:
    role = await repo.get_role(session, role_id)
    if role is None:
        raise RoleNotFound()
    before = {"description": role.description, "permissions": list(role.permissions)}
    if description is not None:
        role.description = description
    if permissions is not None:
        role.permissions = validate_permissions(permissions)
    await _ensure_manager_remains(session)
    record_audit(
        outbox, session, actor, action="role.update", object_type="admin_role", object_id=role.id,
        before=before, after={"description": role.description, "permissions": list(role.permissions)}, ip=ip,
    )  # fmt: skip
    return role


async def delete_role(session: AsyncSession, role_id: uuid.UUID, *, actor: Actor, ip: str | None) -> None:
    role = await repo.get_role(session, role_id)
    if role is None:
        raise RoleNotFound()
    if await repo.role_in_use(session, role_id):
        raise RoleInUse()
    await session.delete(role)
    record_audit(
        outbox, session, actor, action="role.delete", object_type="admin_role", object_id=role_id,
        before={"name": role.name, "permissions": list(role.permissions)}, ip=ip,
    )  # fmt: skip
