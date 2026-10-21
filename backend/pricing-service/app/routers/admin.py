"""/v1/admin/promo-codes — admin JWT + `pricing.manage`."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from medbron_common.audit import Actor

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.models import PromoCode
from app.repositories import pricing as repo
from app.routers.deps import Ip, Session
from app.schemas.pricing import PromoIn, PromoOut, PromoUpdate
from app.services import promos
from app.services.errors import PromoNotFound

router = APIRouter(prefix="/v1/admin/promo-codes", tags=["admin"])

Manager = Annotated[CurrentAdmin, Depends(require_permission(permissions.PRICING_MANAGE))]


async def _out(session: Session, promo: PromoCode) -> PromoOut:
    uses = (await repo.promo_live_uses(session, [promo.id])).get(promo.id, 0)
    return PromoOut.model_validate(promo).model_copy(update={"uses": uses})


@router.get("", response_model=list[PromoOut])
async def list_promos(
    _: Manager,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PromoOut]:
    rows = await repo.list_promos(session, limit=limit, offset=offset)
    uses = await repo.promo_live_uses(session, [r.id for r in rows])
    return [PromoOut.model_validate(r).model_copy(update={"uses": uses.get(r.id, 0)}) for r in rows]


@router.get("/{promo_id}", response_model=PromoOut)
async def get_promo(promo_id: UUID, _: Manager, session: Session) -> PromoOut:
    promo = await session.get(PromoCode, promo_id)
    if promo is None:
        raise PromoNotFound()
    return await _out(session, promo)


@router.post("", response_model=PromoOut, status_code=status.HTTP_201_CREATED)
async def create_promo(body: PromoIn, admin: Manager, session: Session, ip: Ip) -> PromoOut:
    promo = await promos.create(session, body.model_dump(), actor=Actor.admin(admin), ip=ip)
    return await _out(session, promo)


@router.patch("/{promo_id}", response_model=PromoOut)
async def update_promo(promo_id: UUID, body: PromoUpdate, admin: Manager, session: Session, ip: Ip) -> PromoOut:
    promo = await promos.update(session, promo_id, body.model_dump(exclude_unset=True), actor=Actor.admin(admin), ip=ip)
    return await _out(session, promo)
