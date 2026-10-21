"""/v1/admin/users/{id}/profile — admin JWT + `users.manage` (qo'llab-quvvatlash uchun, faqat o'qish)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.repositories import users as repo
from app.routers.deps import Session
from app.schemas.users import AdminProfileOut
from app.services.errors import ProfileDeleted

router = APIRouter(prefix="/v1/admin/users", tags=["admin"])

Manager = Annotated[CurrentAdmin, Depends(require_permission(permissions.USERS_MANAGE))]


@router.get("/{user_id}/profile", response_model=AdminProfileOut)
async def profile(user_id: UUID, _: Manager, session: Session) -> AdminProfileOut:
    row = await repo.get_profile(session, user_id)
    if row is None:
        raise ProfileDeleted("Profil topilmadi", code="PROFILE_NOT_FOUND")
    return AdminProfileOut(
        user_id=row.user_id,
        full_name=row.full_name,
        preferred_language=row.preferred_language,
        deleted_at=row.deleted_at,
        patients=await repo.count_patients(session, user_id),
        addresses=await repo.count_addresses(session, user_id),
        active_bookings=await repo.active_bookings(session, user_id),
        created_at=row.created_at,
    )
