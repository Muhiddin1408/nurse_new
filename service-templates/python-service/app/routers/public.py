"""/v1/__SHORT__ — user JWT."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.security import CurrentUser, get_current_user
from app.schemas.common import WhoAmI

router = APIRouter(prefix="/v1/__SHORT__", tags=["__SHORT__"])


@router.get("/whoami", response_model=WhoAmI)
async def whoami(user: Annotated[CurrentUser, Depends(get_current_user)]) -> WhoAmI:
    """Shablon namunasi: auth zanjiri ishlayotganini ko'rsatadi. Haqiqiy endpointlar bilan almashtiring."""
    return WhoAmI(user_id=user.user_id, roles=sorted(user.roles))
