"""/v1/reviews (ommaviy o'qish + mijoz), /v1/doctor/reviews (shifokor) — user JWT."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from medbron_common.errors import BadRequest, Forbidden

from app.core.security import CurrentUser, get_current_user
from app.repositories import reviews as repo
from app.routers.deps import DepsDep, Session, my_out, public_out
from app.schemas.reviews import MyReviewOut, PublicReviewOut, ReplyIn, ReviewIn
from app.services import reviews
from app.services.reviews import ReviewInput

router = APIRouter(tags=["reviews"])

User = Annotated[CurrentUser, Depends(get_current_user)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def _doctor(user: CurrentUser) -> UUID:
    if user.doctor_id is None or not user.has_role("doctor"):
        raise Forbidden(code="DOCTOR_ONLY", message="Faqat shifokor uchun")
    return user.doctor_id


@router.post("/v1/reviews", response_model=MyReviewOut, status_code=status.HTTP_201_CREATED)
async def create_review(body: ReviewIn, user: User, session: Session, deps: DepsDep) -> MyReviewOut:
    """Faqat o'z yakunlangan bronga, oyna ichida, bir marta. Bayroqli matn — moderatsiyaga (`pending`)."""
    data = ReviewInput(**body.model_dump())
    return my_out(await reviews.create(session, deps, client_id=user.user_id, data=data))


@router.get("/v1/reviews", response_model=list[PublicReviewOut])
async def list_reviews(
    session: Session,
    doctor_id: UUID | None = None,
    clinic_id: UUID | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> list[PublicReviewOut]:
    """Tokensiz. Faqat e'lon qilingan sharhlar."""
    if doctor_id is None and clinic_id is None:
        raise BadRequest(code="FILTER_REQUIRED", message="doctor_id yoki clinic_id majburiy")
    rows = await repo.published(session, doctor_id=doctor_id, clinic_id=clinic_id, limit=limit, offset=offset)
    return [public_out(r) for r in rows]


@router.get("/v1/reviews/mine", response_model=list[MyReviewOut])
async def my_reviews(user: User, session: Session, limit: Limit = 50) -> list[MyReviewOut]:
    return [my_out(r) for r in await repo.of_client(session, user.user_id, limit=limit)]


@router.get("/v1/doctor/reviews", response_model=list[PublicReviewOut])
async def doctor_reviews(user: User, session: Session, limit: Limit = 20, offset: Offset = 0) -> list[PublicReviewOut]:
    rows = await repo.published(session, doctor_id=_doctor(user), limit=limit, offset=offset)
    return [public_out(r) for r in rows]


@router.post("/v1/doctor/reviews/{review_id}/reply", response_model=PublicReviewOut)
async def reply(review_id: UUID, body: ReplyIn, user: User, session: Session) -> PublicReviewOut:
    """Bir marta; begona sharh — 404."""
    return public_out(await reviews.reply(session, doctor_id=_doctor(user), review_id=review_id, text=body.text))
