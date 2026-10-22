"""/v1/admin/search — admin JWT + `search.manage`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.repositories import projection as repo
from app.routers.deps import Ip, Session
from app.schemas.search import ReindexOut
from app.services import reindex

router = APIRouter(prefix="/v1/admin/search", tags=["admin"])

Manager = Annotated[CurrentAdmin, Depends(require_permission(permissions.SEARCH_MANAGE))]


@router.post("/reindex", response_model=ReindexOut, status_code=status.HTTP_202_ACCEPTED)
async def request_reindex(admin: Manager, session: Session, ip: Ip) -> ReindexOut:
    """Navbatga qo'yadi (scheduler ≤ 5 s ichida boshlaydi). Bir vaqtda bitta — aks holda 409 REINDEX_RUNNING.
    Holat: `GET /v1/admin/search/reindex`."""
    return ReindexOut.model_validate(await reindex.request(session, admin_id=admin.admin_id, ip=ip))


@router.get("/reindex", response_model=list[ReindexOut])
async def reindex_runs(
    _: Manager, session: Session, limit: Annotated[int, Query(ge=1, le=50)] = 10
) -> list[ReindexOut]:
    return [ReindexOut.model_validate(r) for r in await repo.recent_runs(session, limit)]
