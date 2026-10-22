"""Refund oqimi (B4). Manbalar: booking.cancelled / booking.no_show (`refund_tiyin`), booking.confirmation_failed
(saga kompensatsiyasi — to'liq), dispute.resolved, admin.

`request` CHAQIRUVCHINING tranzaksiyasida (event handler / admin so'rovi): faqat Refund(pending) qatori, tarmoq
chaqiruvi YO'Q. Provayderga `process_due` (scheduler, har daqiqa) murojaat qiladi — sekin provayder handler'ni
yoki admin so'rovini osiltirib, qulflarni ushlab turmaydi.

Shifokor ulushi (ADR-0012): `share_of(money, R).doctor`, lekin to'lovdagi shifokor ulushidan oshmaydi
(qisman refundlar yaxlitlashi yig'ilmaydi); oxirgi refund aniq qoldiqni oladi. Nizoda `doctor_liable=false` — 0.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medbron_common.eventbus.envelope import new_event_id
from medbron_common.money import share_of

from app.clients.providers import ManualRefundRequired, ProviderError, RefundResult
from app.core.config import get_settings
from app.metrics import REFUNDS
from app.models import Payment, Refund
from app.models.payment import (
    PARTIALLY_REFUNDED,
    R_FAILED,
    R_MANUAL,
    R_PENDING,
    R_SUCCEEDED,
    REFUNDABLE_STATUSES,
    REFUNDED,
)
from app.repositories import payments as repo
from app.services import ledger, payloads
from app.services.deps import Deps

logger = logging.getLogger(__name__)

# Bir ishchi refund'ni "egallab" turadigan vaqt — shu oraliqda boshqa nusxa uni olmaydi
CLAIM_LEASE = timedelta(minutes=5)


async def _doctor_share(session: AsyncSession, p: Payment, amount: int, remaining: int, liable: bool) -> int:
    if not liable:
        return 0
    captured = share_of(p.money, p.amount_tiyin).doctor_tiyin
    already = sum(r.doctor_share_tiyin for r in await repo.refunds_of(session, p.id))
    left = max(0, captured - already)
    if amount == remaining:
        return left  # oxirgi refund — aniq qoldiq
    return max(0, min(share_of(p.money, amount).doctor_tiyin, left))


async def request(
    session: AsyncSession,
    *,
    booking_id: uuid.UUID,
    amount_tiyin: int,
    source: str,
    source_ref: str,
    reason: str,
    initiated_by: str,
    payment_id: uuid.UUID | None = None,
    doctor_liable: bool = True,
) -> Refund | None:
    """IDEMPOTENT: (source, source_ref) uchun bitta refund. sum(refunds) ≤ to'lov — to'lov qatori qulflanadi."""
    if amount_tiyin <= 0:
        return None
    existing = await repo.refund_by_source(session, source, source_ref)
    if existing is not None:
        return existing
    if payment_id is not None:
        p = await repo.get(session, payment_id, for_update=True)
        if p is not None and p.status not in REFUNDABLE_STATUSES:
            p = None
    else:
        p = await repo.refundable_for(session, booking_id, for_update=True)
    if p is None:
        # Pul yo'q (at_clinic, allaqachon to'liq qaytarilgan yoki provayder o'zi qaytargan) — refund kerak emas
        logger.warning(
            "refund requested but no refundable payment", extra={"booking_id": str(booking_id), "source": source}
        )
        return None
    remaining = p.amount_tiyin - await repo.refunded_total(session, p.id)
    amount = min(amount_tiyin, remaining)
    if amount <= 0:
        return None
    refund = Refund(
        id=new_event_id(),
        payment_id=p.id,
        booking_id=booking_id,
        amount_tiyin=amount,
        doctor_share_tiyin=await _doctor_share(session, p, amount, remaining, doctor_liable),
        source=source,
        source_ref=source_ref,
        reason=reason[:64],
        initiated_by=initiated_by,
        status=R_PENDING,
        next_attempt_at=datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(refund)
            await session.flush()
    except IntegrityError:
        return await repo.refund_by_source(session, source, source_ref)
    REFUNDS.labels(source=source, status="requested").inc()
    logger.info("refund requested", extra={"booking_id": str(booking_id), "amount_tiyin": amount, "source": source})
    return refund


async def mark_succeeded(session: AsyncSession, r: Refund, *, external_refund_id: str = "") -> Refund:
    """Provayder pulni qaytardi (API, kabinetdagi bekor qilish yoki admin tasdig'i). `r` — FOR UPDATE bilan."""
    if r.status == R_SUCCEEDED:
        return r
    r.status = R_SUCCEEDED
    r.external_refund_id = external_refund_id[:64]
    r.completed_at = datetime.now(UTC)
    await session.flush()
    p = await repo.get(session, r.payment_id, for_update=True)
    assert p is not None
    if p.status in REFUNDABLE_STATUSES:
        refunded = await repo.refunded_total(session, p.id, only_succeeded=True)
        p.status = REFUNDED if refunded >= p.amount_tiyin else PARTIALLY_REFUNDED
    await ledger.post_refund(session, r, p)
    payloads.refund_succeeded(session, r, p)
    REFUNDS.labels(source=r.source, status="succeeded").inc()
    return r


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=2**attempts) + timedelta(seconds=random.uniform(0, 30))  # noqa: S311


async def _claim(factory: async_sessionmaker[AsyncSession], limit: int) -> list[uuid.UUID]:
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        rows = await repo.due_refunds(session, now, limit=limit)
        for r in rows:
            r.next_attempt_at = now + CLAIM_LEASE
        return [r.id for r in rows]


async def process_one(factory: async_sessionmaker[AsyncSession], deps: Deps, refund_id: uuid.UUID) -> str:
    async with factory() as session:
        r = await repo.refund(session, refund_id)
        p = await repo.get(session, r.payment_id) if r is not None else None
    if r is None or p is None or r.status != R_PENDING:
        return "skipped"
    outcome: RefundResult | Exception
    try:
        outcome = await deps.provider(p.provider).refund(
            paydoc_id=p.provider_state.get("click_paydoc_id"),
            amount_tiyin=r.amount_tiyin,
            payment_amount_tiyin=p.amount_tiyin,
        )
    except (ManualRefundRequired, ProviderError) as exc:
        outcome = exc

    async with factory() as session, session.begin():
        locked = await repo.refund(session, refund_id, for_update=True)
        if locked is None or locked.status != R_PENDING:
            return "skipped"
        if isinstance(outcome, RefundResult):
            await mark_succeeded(session, locked, external_refund_id=outcome.external_refund_id)
            return R_SUCCEEDED
        locked.last_error = str(outcome)[:2000]
        if isinstance(outcome, ManualRefundRequired):
            locked.status = R_MANUAL
            REFUNDS.labels(source=locked.source, status=R_MANUAL).inc()
            logger.error(
                "ALERT: refund requires manual action", extra={"refund_id": str(locked.id), "error": str(outcome)}
            )
            return R_MANUAL
        locked.attempts += 1
        if locked.attempts >= get_settings().refund_max_attempts:
            locked.status = R_FAILED
            payloads.refund_failed(session, locked)
            REFUNDS.labels(source=locked.source, status=R_FAILED).inc()
            logger.error("ALERT: refund FAILED", extra={"refund_id": str(locked.id), "attempts": locked.attempts})
            return R_FAILED
        locked.next_attempt_at = datetime.now(UTC) + _backoff(locked.attempts)
        return "retry"


async def process_due(factory: async_sessionmaker[AsyncSession], deps: Deps, *, limit: int = 100) -> dict[str, int]:
    results: dict[str, int] = {}
    for refund_id in await _claim(factory, limit):
        result = await process_one(factory, deps, refund_id)
        results[result] = results.get(result, 0) + 1
    return results
