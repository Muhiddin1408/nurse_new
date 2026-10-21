"""/v1/admin/{payments,refunds,ledger,reconciliation} — admin JWT + permission (`payments.read`, `payments.refund`)."""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.repositories import payments as repo
from app.routers.deps import Ip, Session
from app.schemas.payments import (
    AdminPaymentDetail,
    AdminPaymentOut,
    BalancesOut,
    CompleteRefundIn,
    DiscrepancyOut,
    LedgerEntryOut,
    LedgerTransactionOut,
    RefundIn,
    RefundOut,
    ResolveIn,
)
from app.services import admin_ops, ledger
from app.services.errors import InvalidRegistry, PaymentNotFound

router = APIRouter(prefix="/v1/admin", tags=["admin"])

Reader = Annotated[CurrentAdmin, Depends(require_permission(permissions.PAYMENTS_READ))]
Refunder = Annotated[CurrentAdmin, Depends(require_permission(permissions.PAYMENTS_REFUND))]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
MAX_REGISTRY_BYTES = 20 * 1024 * 1024


@router.get("/payments", response_model=list[AdminPaymentOut])
async def list_payments(
    _: Reader,
    session: Session,
    booking_id: UUID | None = None,
    status: str | None = None,
    provider: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[AdminPaymentOut]:
    rows = await repo.search(
        session, booking_id=booking_id, status=status, provider=provider, limit=limit, offset=offset
    )
    return [AdminPaymentOut.model_validate(p) for p in rows]


@router.get("/payments/{payment_id}", response_model=AdminPaymentDetail)
async def get_payment(payment_id: UUID, _: Reader, session: Session) -> AdminPaymentDetail:
    p = await repo.get(session, payment_id)
    if p is None:
        raise PaymentNotFound()
    detail = AdminPaymentDetail.model_validate(p)
    detail.refunds = [RefundOut.model_validate(r) for r in await repo.refunds_of(session, p.id)]
    return detail


@router.get("/refunds", response_model=list[RefundOut])
async def list_refunds(
    _: Reader,
    session: Session,
    status: str | None = Query(None, description="pending | succeeded | failed | manual_required"),
    booking_id: UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[RefundOut]:
    rows = await repo.search_refunds(session, status=status, booking_id=booking_id, limit=limit, offset=offset)
    return [RefundOut.model_validate(r) for r in rows]


@router.post("/refunds", response_model=RefundOut)
async def create_refund(
    body: RefundIn,
    admin: Refunder,
    session: Session,
    ip: Ip,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=64)],
) -> RefundOut:
    """Qo'lda refund (navbatga qo'yiladi, provayderga scheduler yuboradi). `Idempotency-Key` majburiy."""
    r = await admin_ops.create_refund(
        session,
        admin,
        payment_id=body.payment_id,
        amount_tiyin=body.amount_tiyin,
        reason=body.reason,
        idempotency_key=idempotency_key,
        ip=ip,
    )
    return RefundOut.model_validate(r)


@router.post("/refunds/{refund_id}/complete", response_model=RefundOut)
async def complete_refund(
    refund_id: UUID, body: CompleteRefundIn, admin: Refunder, session: Session, ip: Ip
) -> RefundOut:
    """Provayder kabinetida qo'lda qaytarilganini tasdiqlash (manual_required / failed → succeeded)."""
    r = await admin_ops.complete_manual(
        session, admin, refund_id, external_ref=body.external_ref, note=body.note, ip=ip
    )
    return RefundOut.model_validate(r)


@router.post("/refunds/{refund_id}/retry", response_model=RefundOut)
async def retry_refund(refund_id: UUID, admin: Refunder, session: Session, ip: Ip) -> RefundOut:
    return RefundOut.model_validate(await admin_ops.retry_refund(session, admin, refund_id, ip=ip))


@router.get("/ledger", response_model=list[LedgerTransactionOut])
async def ledger_journal(
    _: Reader, session: Session, booking_id: UUID | None = None, limit: Limit = 50, offset: Offset = 0
) -> list[LedgerTransactionOut]:
    return [
        LedgerTransactionOut(
            id=t.id,
            kind=t.kind,
            ref_type=t.ref_type,
            ref_id=t.ref_id,
            booking_id=t.booking_id,
            description=t.description,
            created_at=t.created_at,
            entries=[LedgerEntryOut.model_validate(e) for e in entries],
        )
        for t, entries in await repo.ledger_page(session, booking_id=booking_id, limit=limit, offset=offset)
    ]


@router.get("/ledger/balances", response_model=BalancesOut)
async def ledger_balances(_: Reader, session: Session, doctor_id: UUID | None = None) -> BalancesOut:
    """Hisob qoldiqlari; `doctor_id` bilan — "shu shifokorga qancha qarzdormiz" (doctor_payable)."""
    return BalancesOut(
        doctor_id=doctor_id,
        balances=await ledger.balances(session, doctor_id=doctor_id),
        trial_balance=await repo.trial_balance(session),
    )


@router.get("/reconciliation", response_model=list[DiscrepancyOut])
async def list_discrepancies(
    _: Reader,
    session: Session,
    status: str | None = Query("open", description="open | resolved; bo'sh — hammasi"),
    type: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[DiscrepancyOut]:
    rows = await repo.search_discrepancies(session, status=status or None, type_=type, limit=limit, offset=offset)
    return [DiscrepancyOut.model_validate(d) for d in rows]


@router.post("/reconciliation/registry", response_model=list[DiscrepancyOut])
async def import_registry(
    admin: Reader,
    session: Session,
    ip: Ip,
    request: Request,
    provider: Annotated[str, Query(pattern="^(payme|click)$")],
    day: date,
) -> list[DiscrepancyOut]:
    """Provayder kabinetidan olingan kunlik reestr bilan solishtirish. Tana — `text/csv` (UTF-8, sarlavha bilan):
    `id, amount (tiyin), state, booking_id`. Hajm ≤ 20 MB."""
    body = await request.body()
    if len(body) > MAX_REGISTRY_BYTES:
        raise InvalidRegistry("Reestr juda katta (> 20 MB)")
    try:
        csv_text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidRegistry("Reestr UTF-8 bo'lishi kerak") from None
    found = await admin_ops.import_registry(session, admin, provider=provider, day=day, csv_text=csv_text, ip=ip)
    return [DiscrepancyOut.model_validate(d) for d in found]


@router.post("/reconciliation/{discrepancy_id}/resolve", response_model=DiscrepancyOut)
async def resolve_discrepancy(
    discrepancy_id: UUID, body: ResolveIn, admin: Refunder, session: Session, ip: Ip
) -> DiscrepancyOut:
    return DiscrepancyOut.model_validate(
        await admin_ops.resolve_discrepancy(session, admin, discrepancy_id, note=body.note, ip=ip)
    )
