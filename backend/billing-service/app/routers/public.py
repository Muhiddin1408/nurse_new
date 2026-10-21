"""/v1/doctor/* (shifokor — JWT `doctor_id` claim) va /v1/clinic/{clinic_id}/earnings (klinika admini —
a'zolik catalog `/internal/v1/access` dan, Redis 60 s). Begona payout / klinika — 404."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from medbron_common.errors import Forbidden

from app.core.config import get_settings
from app.core.redis import get_redis
from app.core.security import CurrentUser, get_current_user
from app.repositories import entries as entries_repo
from app.repositories import payouts as repo
from app.routers.deps import DepsDep, Ip, Session
from app.schemas.billing import EarningsOut, PayoutAccountIn, PayoutAccountOut, PayoutOut
from app.services import accounts, earnings, payouts
from app.services.deps import Deps
from app.services.errors import ClinicNotFound, PayoutAccountMissing, PayoutNotFound

router = APIRouter(tags=["earnings"])
User = Annotated[CurrentUser, Depends(get_current_user)]
Period = Annotated[Literal["today", "week", "month"] | None, Query()]
From = Annotated[date | None, Query(alias="from")]
To = Annotated[date | None, Query(alias="to")]


def _doctor_id(user: User) -> UUID:
    if user.doctor_id is None:
        raise Forbidden(code="NOT_A_DOCTOR", message="Faqat shifokorlar uchun")
    return user.doctor_id


DoctorId = Annotated[UUID, Depends(_doctor_id)]


def csv_response(body: str, filename: str) -> Response:
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/v1/doctor/earnings", response_model=EarningsOut)
async def doctor_earnings(
    doctor_id: DoctorId, session: Session, period: Period = None, date_from: From = None, date_to: To = None
) -> dict[str, object]:
    frm, to = earnings.resolve_range(period, date_from, date_to)
    return await earnings.summary(session, frm=frm, to=to, doctor_id=doctor_id)


@router.get("/v1/doctor/payouts", response_model=list[PayoutOut])
async def doctor_payouts(
    doctor_id: DoctorId,
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PayoutOut]:
    rows = await repo.list_(session, doctor_id=doctor_id, limit=limit, offset=offset)
    return [PayoutOut.model_validate(p) for p in rows]


@router.get("/v1/doctor/payouts/{payout_id}/statement")
async def doctor_payout_statement(payout_id: UUID, doctor_id: DoctorId, session: Session) -> Response:
    payout = await repo.get(session, payout_id)
    if payout is None or payout.doctor_id != doctor_id:
        raise PayoutNotFound()
    rows = await entries_repo.of_payout(session, payout.id)
    return csv_response(payouts.statement_csv(payout, rows), f"payout-{payout.period_start}.csv")


@router.get("/v1/doctor/payout-account", response_model=PayoutAccountOut)
async def get_payout_account(doctor_id: DoctorId, session: Session) -> dict[str, str]:
    account = await repo.account(session, doctor_id)
    if account is None:
        raise PayoutAccountMissing()
    return account.as_dict()


@router.put("/v1/doctor/payout-account", response_model=PayoutAccountOut)
async def put_payout_account(
    body: PayoutAccountIn, doctor_id: DoctorId, user: User, session: Session, ip: Ip
) -> dict[str, str]:
    account = await accounts.save(session, user, doctor_id, body.model_dump(), ip=ip)
    return account.as_dict()


async def _require_clinic_admin(request: Request, deps: Deps, user: CurrentUser, clinic_id: UUID) -> None:
    key = f"access:{user.user_id}:{clinic_id}"
    redis = get_redis() if request.app.state.has_redis else None
    cached = await redis.get(key) if redis is not None else None
    if cached is not None:
        allowed = json.loads(cached)
    else:
        access = await deps.catalog.access(user.user_id, clinic_id=clinic_id)
        # Klinika daromadi — faqat klinika admini (shifokor a'zo o'z daromadini /v1/doctor da ko'radi)
        allowed = bool(access["allowed"]) and access.get("role") == "clinic_admin"
        if redis is not None:
            await redis.set(key, json.dumps(allowed), ex=get_settings().access_cache_seconds)
    if not allowed:
        raise ClinicNotFound()


@router.get("/v1/clinic/{clinic_id}/earnings", response_model=EarningsOut)
async def clinic_earnings(
    clinic_id: UUID,
    request: Request,
    user: User,
    session: Session,
    deps: DepsDep,
    period: Period = None,
    date_from: From = None,
    date_to: To = None,
) -> dict[str, object]:
    await _require_clinic_admin(request, deps, user, clinic_id)
    frm, to = earnings.resolve_range(period, date_from, date_to)
    return await earnings.summary(session, frm=frm, to=to, clinic_id=clinic_id)
