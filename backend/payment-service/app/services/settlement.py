"""Pul yechildi hodisasining provayderdan mustaqil qismi (B6): Payme `PerformTransaction` va Click `Complete`
aynan bir xil ishni qiladi — bitta funksiya, aks holda birinchi tuzatish ikkinchisini unutadi.

Mikroservisda bron SINXRON tasdiqlanmaydi (ADR-0006): to'lov `succeeded` + ledger + outbox(`payment.captured`)
BITTA tranzaksiyada, provayderga darhol muvaffaqiyat. Booking `payment.captured` ni olib slotni tasdiqlaydi;
tasdiqlay olmasa `booking.confirmation_failed` → shu yerda to'liq refund (saga kompensatsiyasi).
Chaqiruvchi tranzaksiya ichida va to'lov qatori QULFLANGAN holda chaqiradi.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics import CAPTURE_DURATION, CAPTURED
from app.models import Payment
from app.models.payment import SUCCEEDED
from app.repositories import payments as repo
from app.services import ledger, payloads


class AlreadyPaidElsewhere(Exception):
    """Bron boshqa to'lov bilan allaqachon to'langan — ikkinchi marta yechilmaydi."""


async def settle_success(
    session: AsyncSession, p: Payment, *, perform_time: int, extra_state: dict[str, Any] | None = None
) -> None:
    other = await repo.money_taken_for(session, p.booking_id)
    if other is not None and other.id != p.id:
        raise AlreadyPaidElsewhere("Buyurtma allaqachon to'langan")
    p.status = SUCCEEDED
    p.perform_time = perform_time
    p.captured_at = datetime.now(UTC)
    if extra_state:
        p.provider_state = {**p.provider_state, **extra_state}
    await session.flush()  # uq_payments_money_taken_per_booking — parallel Perform'da shu yerda yiqiladi
    await ledger.post_capture(session, p)
    payloads.captured(session, p)


def observe_success(p: Payment) -> None:
    """Commit'dan KEYIN (rollback bo'lsa metrika yolg'on bo'lmasin)."""
    CAPTURED.labels(provider=p.provider).inc()
    if p.captured_at is not None:
        CAPTURE_DURATION.labels(provider=p.provider).observe(max(0.0, (p.captured_at - p.created_at).total_seconds()))
