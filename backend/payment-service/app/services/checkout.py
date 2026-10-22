"""Checkout (B1): bron uchun to'lov urinishini ochadi va provayder havolasini qaytaradi.

IDEMPOTENT: shu bron + provayder uchun ochiq (`created`/`processing`) to'lov bo'lsa — o'sha qaytariladi.
Bir nechta urinish mumkin (Payme bekor bo'ldi → Click), lekin pul faqat bir marta yechiladi
(`uq_payments_money_taken_per_booking`). Parallel checkout'lar bron bo'yicha advisory lock bilan navbatlanadi.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.db import transaction
from medbron_common.eventbus.envelope import new_event_id

from app.clients.upstream import Payable
from app.core.config import get_settings
from app.metrics import CHECKOUTS
from app.models import Payment
from app.models.payment import CLICK, CREATED, PAYME
from app.repositories import payments as repo
from app.services import fiscal, payloads
from app.services.deps import Deps
from app.services.errors import BookingNotFound, CheckoutNotAllowed


@dataclass(frozen=True)
class Checkout:
    payment: Payment
    checkout_url: str
    expires_at: datetime | None


def _som(amount_tiyin: int) -> str:
    som, tiyin = divmod(amount_tiyin, 100)
    return f"{som}.{tiyin:02d}"


def checkout_url(p: Payment) -> str:
    s = get_settings()
    if p.provider == PAYME:
        params = f"m={s.payme_merchant_id};ac.booking_id={p.booking_id};a={p.amount_tiyin}"
        return f"{s.payme_checkout_url}/{base64.b64encode(params.encode()).decode()}"
    return (
        f"{s.click_checkout_url}?service_id={s.click_service_id}&merchant_id={s.click_merchant_id}"
        f"&amount={_som(p.amount_tiyin)}&transaction_param={p.booking_id}"
    )


def ensure_payable(b: Payable) -> None:
    if not b.payable:
        raise CheckoutNotAllowed("To'lov muddati o'tgan yoki bron to'lov kutish holatida emas")
    # B10: klinikada to'lanadigan bron onlayn to'lanmaydi (u allaqachon tasdiqlangan)
    if b.prepay_tiyin <= 0:
        raise CheckoutNotAllowed("Bu bron klinikada to'lanadi")


def new_payment(b: Payable, provider: str) -> Payment:
    # B10: onlayn summa — `prepay_tiyin` (zakladda qolgani klinikada)
    return Payment(
        id=new_event_id(),
        booking_id=b.booking_id,
        booking_number=b.number,
        client_id=b.client_id,
        doctor_id=b.doctor_id,
        clinic_id=b.clinic_id,
        provider=provider,
        amount_tiyin=b.prepay_tiyin,
        status=CREATED,
        provider_state={},
        money=b.money,
        receipt_items=fiscal.receipt_items(b.items, b.prepay_tiyin),
    )


async def lock_booking(session: AsyncSession, booking_id: uuid.UUID) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(booking_id), 0))))


async def create(
    session: AsyncSession, deps: Deps, *, client_id: uuid.UUID, booking_id: uuid.UUID, provider: str
) -> Checkout:
    if provider not in (PAYME, CLICK):
        raise CheckoutNotAllowed(f"Noma'lum provayder: {provider}", code="UNKNOWN_PROVIDER")
    b = await deps.booking.payable(booking_id)
    if b is None or b.client_id != client_id:
        raise BookingNotFound()
    ensure_payable(b)
    async with transaction(session):
        await lock_booking(session, booking_id)
        if await repo.money_taken_for(session, booking_id) is not None:
            raise CheckoutNotAllowed("Bron allaqachon to'langan", code="ALREADY_PAID")
        payment = await repo.open_for(session, booking_id, provider)
        if payment is None:
            payment = new_payment(b, provider)
            session.add(payment)
            await session.flush()
            payloads.created(session, payment)
            CHECKOUTS.labels(provider=provider).inc()
    return Checkout(payment=payment, checkout_url=checkout_url(payment), expires_at=b.payment_due_at)
