"""/v1/admin/{admins,roles,permissions,audit,settings} — admin JWT + permission."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from medbron_common.audit import Actor
from medbron_common.db import transaction

from app.core import permissions as perms
from app.core.security import CurrentAdmin, require_permission
from app.repositories import admin as repo
from app.routers.deps import Ip, Session
from app.schemas.admin import (
    AdminCreate,
    AdminDetail,
    AdminOut,
    AdminUpdate,
    AuditOut,
    AuditPage,
    EnrollmentOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    SettingsOut,
    SettingsUpdate,
)
from app.services import admins, platform
from app.services.errors import InvalidSettings

router = APIRouter(prefix="/v1/admin", tags=["admin"])

Manager = Annotated[CurrentAdmin, Depends(require_permission(perms.ADMINS_MANAGE))]
AuditReader = Annotated[CurrentAdmin, Depends(require_permission(perms.AUDIT_READ))]
SettingsManager = Annotated[CurrentAdmin, Depends(require_permission(perms.SETTINGS_MANAGE))]


async def _detail(session: Session, admin_id: UUID) -> AdminDetail:
    row = await repo.get_admin(session, admin_id)
    assert row is not None
    return AdminDetail(
        **AdminOut.model_validate(row).model_dump(),
        role_ids=await repo.role_ids_of(session, admin_id),
        permissions=await repo.permissions_of(session, admin_id),
    )


# --- admins ----------------------------------------------------------------------------------


@router.get("/admins", response_model=list[AdminDetail])
async def list_admins(_: Manager, session: Session) -> list[AdminDetail]:
    return [await _detail(session, a.id) for a in await repo.list_admins(session)]


@router.post("/admins", response_model=EnrollmentOut, status_code=status.HTTP_201_CREATED)
async def create_admin(body: AdminCreate, admin: Manager, session: Session, ip: Ip) -> EnrollmentOut:
    async with transaction(session):
        enrollment = await admins.create_admin(
            session,
            login=body.login,
            full_name=body.full_name,
            password=body.password,
            role_ids=body.role_ids,
            actor=Actor.admin(admin),
            ip=ip,
        )
    return EnrollmentOut(admin=AdminOut.model_validate(enrollment.admin), totp_uri=enrollment.totp_uri)


@router.patch("/admins/{admin_id}", response_model=AdminDetail)
async def update_admin(admin_id: UUID, body: AdminUpdate, admin: Manager, session: Session, ip: Ip) -> AdminDetail:
    async with transaction(session):
        await admins.update_admin(
            session,
            admin_id,
            full_name=body.full_name,
            is_active=body.is_active,
            role_ids=body.role_ids,
            actor=Actor.admin(admin),
            ip=ip,
        )
    return await _detail(session, admin_id)


@router.post("/admins/{admin_id}/reset-totp", response_model=EnrollmentOut)
async def reset_totp(admin_id: UUID, admin: Manager, session: Session, ip: Ip) -> EnrollmentOut:
    async with transaction(session):
        enrollment = await admins.reset_totp(session, admin_id, actor=Actor.admin(admin), ip=ip)
    return EnrollmentOut(admin=AdminOut.model_validate(enrollment.admin), totp_uri=enrollment.totp_uri)


# --- roles -----------------------------------------------------------------------------------


@router.get("/permissions")
async def list_permissions(_: Manager) -> dict[str, str]:
    return perms.PERMISSIONS


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(_: Manager, session: Session) -> list[RoleOut]:
    return [RoleOut.model_validate(r) for r in await repo.list_roles(session)]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(body: RoleCreate, admin: Manager, session: Session, ip: Ip) -> RoleOut:
    try:
        async with transaction(session):
            role = await admins.create_role(
                session,
                name=body.name,
                description=body.description,
                permissions=body.permissions,
                actor=Actor.admin(admin),
                ip=ip,
            )
    except ValueError as exc:
        raise InvalidSettings(str(exc), code="UNKNOWN_PERMISSION") from exc
    return RoleOut.model_validate(role)


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: UUID, body: RoleUpdate, admin: Manager, session: Session, ip: Ip) -> RoleOut:
    try:
        async with transaction(session):
            role = await admins.update_role(
                session,
                role_id,
                description=body.description,
                permissions=body.permissions,
                actor=Actor.admin(admin),
                ip=ip,
            )
    except ValueError as exc:
        raise InvalidSettings(str(exc), code="UNKNOWN_PERMISSION") from exc
    return RoleOut.model_validate(role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: UUID, admin: Manager, session: Session, ip: Ip) -> Response:
    async with transaction(session):
        await admins.delete_role(session, role_id, actor=Actor.admin(admin), ip=ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- audit -----------------------------------------------------------------------------------


@router.get("/audit", response_model=AuditPage)
async def audit(
    _: AuditReader,
    session: Session,
    actor_id: UUID | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
    producer: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    rows = await repo.query_audit(
        session,
        actor_id=actor_id,
        object_type=object_type,
        object_id=object_id,
        action=action,
        producer=producer,
        since=since,
        until=until,
        before_id=before_id,
        limit=limit,
    )
    items = [AuditOut.model_validate(r) for r in rows]
    return AuditPage(items=items, next_before_id=items[-1].id if len(items) == limit else None)


# --- settings --------------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsOut)
async def get_settings(_: SettingsManager, session: Session, response: Response) -> SettingsOut:
    snapshot = await platform.current(session)
    response.headers["ETag"] = platform.etag(snapshot.version)
    return SettingsOut(version=snapshot.version, values=snapshot.values.model_dump())


@router.put("/settings", response_model=SettingsOut)
async def put_settings(
    body: SettingsUpdate, admin: SettingsManager, session: Session, ip: Ip, response: Response
) -> SettingsOut:
    """Faqat yuborilgan bo'limlar va ulardagi yuborilgan maydonlar o'zgaradi. `expected_version` — joriy
    versiya (boshqa admin o'zgartirgan bo'lsa 409)."""
    async with transaction(session):
        snapshot = await platform.update(
            session, body.values, expected_version=body.expected_version, actor=Actor.admin(admin), ip=ip
        )
    response.headers["ETag"] = platform.etag(snapshot.version)
    return SettingsOut(version=snapshot.version, values=snapshot.values.model_dump())
