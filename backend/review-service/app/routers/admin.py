"""/v1/admin/reviews — admin JWT + `reviews.moderate`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.repositories import reviews as repo
from app.routers.deps import Ip, Session
from app.schemas.reviews import AdminReviewOut, RejectIn
from app.services import reviews

router = APIRouter(prefix="/v1/admin/reviews", tags=["admin"])

Moderator = Annotated[CurrentAdmin, Depends(require_permission(permissions.REVIEWS_MODERATE))]


@router.get("", response_model=list[AdminReviewOut])
async def list_reviews(
    _: Moderator,
    session: Session,
    status: str | None = Query("pending", description="pending | published | rejected; bo'sh — hammasi"),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminReviewOut]:
    rows = await repo.by_status(session, status or None, limit=limit, offset=offset)
    return [AdminReviewOut.model_validate(r) for r in rows]


@router.post("/{review_id}/publish", response_model=AdminReviewOut)
async def publish(review_id: UUID, admin: Moderator, session: Session, ip: Ip) -> AdminReviewOut:
    r = await reviews.moderate(session, admin, review_id, publish=True, reason="", ip=ip)
    return AdminReviewOut.model_validate(r)


@router.post("/{review_id}/reject", response_model=AdminReviewOut)
async def reject(review_id: UUID, body: RejectIn, admin: Moderator, session: Session, ip: Ip) -> AdminReviewOut:
    """`pending` yoki `published` (shikoyat bo'yicha olib tashlash) — sabab mijozga yuboriladi."""
    r = await reviews.moderate(session, admin, review_id, publish=False, reason=body.reason, ip=ip)
    return AdminReviewOut.model_validate(r)
