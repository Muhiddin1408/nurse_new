"""/v1/admin/__SHORT__ — admin JWT + permission."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission

router = APIRouter(prefix="/v1/admin/__SHORT__", tags=["admin"])


@router.get("/whoami")
async def admin_whoami(
    admin: Annotated[CurrentAdmin, Depends(require_permission(permissions.READ))],
) -> dict[str, str]:
    return {"admin_id": str(admin.admin_id)}
