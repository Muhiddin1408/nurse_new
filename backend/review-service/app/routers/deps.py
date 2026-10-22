from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip

from app.core.database import get_session
from app.models import Review
from app.schemas.reviews import MyReviewOut, PublicReviewOut
from app.services.deps import Deps

Session = Annotated[AsyncSession, Depends(get_session)]


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


def get_ip(request: Request) -> str | None:
    return client_ip(request)


DepsDep = Annotated[Deps, Depends(get_deps)]
Ip = Annotated[str | None, Depends(get_ip)]


def public_out(r: Review) -> PublicReviewOut:
    return PublicReviewOut(
        id=r.id,
        doctor_id=r.doctor_id,
        rating=r.rating,
        comment=r.comment,
        author=None if r.is_anonymous or not r.author_name else r.author_name,
        created_at=r.created_at,
        doctor_reply=r.doctor_reply or None,
        doctor_replied_at=r.doctor_replied_at,
    )


def my_out(r: Review) -> MyReviewOut:
    return MyReviewOut(
        **public_out(r).model_dump(), booking_id=r.booking_id, status=r.status, reject_reason=r.reject_reason
    )
