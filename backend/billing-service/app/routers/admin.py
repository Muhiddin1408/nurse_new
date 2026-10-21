"""/v1/admin/payouts — admin JWT + `payouts.manage` (ko'rish ham, hisoblash ham, to'lov belgisi ham)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.events import outbox
from app.repositories import entries as entries_repo
from app.repositories import payouts as repo
from app.routers.deps import DepsDep, Ip, Session
from app.routers.public import csv_response
from app.schemas.billing import BuildIn, MarkPaidIn, PayoutOut
from app.services import payouts
from app.services.errors import PayoutNotFound

router = APIRouter(prefix="/v1/admin/payouts", tags=["admin"])
Manager = Annotated[CurrentAdmin, Depends(require_permission(permissions.PAYOUTS_MANAGE))]


@router.get("", response_model=list[PayoutOut])
async def list_payouts(
    _: Manager,
    session: Session,
    status_: Annotated[Literal["pending", "paid"] | None, Query(alias="status")] = None,
    doctor_id: UUID | None = None,
    period_end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PayoutOut]:
    rows = await repo.list_(
        session, doctor_id=doctor_id, status=status_, period_end=period_end, limit=limit, offset=offset
    )
    return [PayoutOut.model_validate(p) for p in rows]


@router.post("/build", response_model=list[PayoutOut])
async def build_payouts(body: BuildIn, admin: Manager, session: Session, deps: DepsDep, ip: Ip) -> list[PayoutOut]:
    """Qo'lda hisoblash (cron o'tkazib yuborilgan bo'lsa). Idempotent — qayta chaqirish dublikat bermaydi."""
    created = await payouts.build(session, deps, body.period_end)
    async with transaction(session):
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="payout.build",
            object_type="payout_period",
            object_id=str(body.period_end or "last_week"),
            after={"created": len(created)},
            ip=ip,
        )
    return [PayoutOut.model_validate(p) for p in created]


@router.post("/{payout_id}/mark-paid", response_model=PayoutOut)
async def mark_paid(payout_id: UUID, body: MarkPaidIn, admin: Manager, session: Session, ip: Ip) -> PayoutOut:
    payout = await payouts.mark_paid(session, payout_id, admin=admin, bank_reference=body.bank_reference.strip(), ip=ip)
    return PayoutOut.model_validate(payout)


@router.get("/{payout_id}/statement")
async def statement(payout_id: UUID, _: Manager, session: Session) -> Response:
    payout = await repo.get(session, payout_id)
    if payout is None:
        raise PayoutNotFound()
    rows = await entries_repo.of_payout(session, payout.id)
    return csv_response(payouts.statement_csv(payout, rows), f"payout-{payout.doctor_id}-{payout.period_start}.csv")
