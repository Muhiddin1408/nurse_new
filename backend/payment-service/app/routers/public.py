"""/v1/payments — mijoz (user JWT). Begona to'lov/bron — 404 (A4)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.security import CurrentUser, get_current_user
from app.repositories import payments as repo
from app.routers.deps import DepsDep, Session
from app.schemas.payments import CheckoutIn, CheckoutOut, PaymentOut
from app.services import checkout
from app.services.errors import PaymentNotFound

router = APIRouter(tags=["payments"])

User = Annotated[CurrentUser, Depends(get_current_user)]


@router.post("/v1/payments", response_model=CheckoutOut)
async def create_checkout(body: CheckoutIn, user: User, session: Session, deps: DepsDep) -> CheckoutOut:
    """To'lovni boshlash. Bron `pending_payment` va muddati o'tmagan bo'lishi shart (aks holda 409 NOT_PAYABLE).
    Idempotent: shu provayder uchun ochiq to'lov bo'lsa — o'sha qaytariladi."""
    result = await checkout.create(
        session, deps, client_id=user.user_id, booking_id=body.booking_id, provider=body.provider
    )
    return CheckoutOut(
        payment=PaymentOut.model_validate(result.payment),
        checkout_url=result.checkout_url,
        expires_at=result.expires_at,
    )


@router.get("/v1/payments", response_model=list[PaymentOut])
async def my_payments(
    user: User, session: Session, booking_id: UUID | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[PaymentOut]:
    rows = await repo.list_for_client(session, user.user_id, booking_id=booking_id, limit=limit)
    return [PaymentOut.model_validate(p) for p in rows]


@router.get("/v1/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(payment_id: UUID, user: User, session: Session) -> PaymentOut:
    p = await repo.get(session, payment_id)
    if p is None or p.client_id != user.user_id:
        raise PaymentNotFound()
    return PaymentOut.model_validate(p)
