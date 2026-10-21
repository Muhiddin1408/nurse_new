"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Discrepancy, LedgerEntry, LedgerTransaction, Payment, ProviderLog, Refund
from app.models.payment import (
    CLICK,
    MONEY_TAKEN_STATUSES,
    OPEN_STATUSES,
    R_SUCCEEDED,
    REFUNDABLE_STATUSES,
    SUCCEEDED,
)

# --------------------------------------------------------------------------- payments


def _locked(stmt: Any) -> Any:
    """FOR UPDATE + identity map'dagi obyektni qulf ostidagi qiymatlar bilan YANGILASH (aks holda eski holat qoladi)."""
    return stmt.with_for_update().execution_options(populate_existing=True)


async def get(session: AsyncSession, payment_id: uuid.UUID, *, for_update: bool = False) -> Payment | None:
    stmt = select(Payment).where(Payment.id == payment_id)
    if for_update:
        await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni bazadagisi bilan almashtirmasin
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).one_or_none()


async def by_external(
    session: AsyncSession, provider: str, external_id: str, *, for_update: bool = False
) -> Payment | None:
    stmt = select(Payment).where(Payment.provider == provider, Payment.external_id == external_id)
    if for_update:
        await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni bazadagisi bilan almashtirmasin
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).one_or_none()


async def open_for(
    session: AsyncSession,
    booking_id: uuid.UUID,
    provider: str,
    *,
    statuses: Sequence[str] = OPEN_STATUSES,
    for_update: bool = False,
) -> Payment | None:
    stmt = (
        select(Payment)
        .where(Payment.booking_id == booking_id, Payment.provider == provider, Payment.status.in_(statuses))
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    if for_update:
        await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni bazadagisi bilan almashtirmasin
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).first()


async def money_taken_for(session: AsyncSession, booking_id: uuid.UUID) -> Payment | None:
    stmt = select(Payment).where(Payment.booking_id == booking_id, Payment.status.in_(MONEY_TAKEN_STATUSES))
    return (await session.scalars(stmt)).first()


async def refundable_for(session: AsyncSession, booking_id: uuid.UUID, *, for_update: bool = False) -> Payment | None:
    stmt = select(Payment).where(Payment.booking_id == booking_id, Payment.status.in_(REFUNDABLE_STATUSES))
    if for_update:
        await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni bazadagisi bilan almashtirmasin
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).first()


async def list_for_client(
    session: AsyncSession, client_id: uuid.UUID, *, booking_id: uuid.UUID | None, limit: int
) -> list[Payment]:
    stmt = select(Payment).where(Payment.client_id == client_id)
    if booking_id is not None:
        stmt = stmt.where(Payment.booking_id == booking_id)
    stmt = stmt.order_by(Payment.created_at.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())


async def search(
    session: AsyncSession,
    *,
    booking_id: uuid.UUID | None = None,
    status: str | None = None,
    provider: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Payment]:
    stmt = select(Payment)
    if booking_id is not None:
        stmt = stmt.where(Payment.booking_id == booking_id)
    if status:
        stmt = stmt.where(Payment.status == status)
    if provider:
        stmt = stmt.where(Payment.provider == provider)
    stmt = stmt.order_by(Payment.created_at.desc()).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def in_window(session: AsyncSession, provider: str, frm_ms: int, to_ms: int) -> list[Payment]:
    stmt = (
        select(Payment)
        .where(
            Payment.provider == provider,
            Payment.provider_create_time >= frm_ms,
            Payment.provider_create_time <= to_ms,
        )
        .order_by(Payment.provider_create_time)
    )
    return list((await session.scalars(stmt)).all())


async def stale_open(session: AsyncSession, before: datetime, *, limit: int) -> list[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.status.in_(OPEN_STATUSES), Payment.created_at < before)
        .order_by(Payment.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def fiscal_pending(session: AsyncSession, since: datetime, *, limit: int) -> list[Payment]:
    stmt = (
        select(Payment)
        .where(
            Payment.provider == CLICK,
            Payment.status == SUCCEEDED,
            Payment.fiscal_submitted_at.is_(None),
            Payment.updated_at >= since,
        )
        .order_by(Payment.updated_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


# --------------------------------------------------------------------------- refunds


async def refund(session: AsyncSession, refund_id: uuid.UUID, *, for_update: bool = False) -> Refund | None:
    stmt = select(Refund).where(Refund.id == refund_id)
    if for_update:
        await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni bazadagisi bilan almashtirmasin
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).one_or_none()


async def refund_by_source(session: AsyncSession, source: str, source_ref: str) -> Refund | None:
    stmt = select(Refund).where(Refund.source == source, Refund.source_ref == source_ref)
    return (await session.scalars(stmt)).one_or_none()


async def refunds_of(session: AsyncSession, payment_id: uuid.UUID) -> list[Refund]:
    stmt = select(Refund).where(Refund.payment_id == payment_id).order_by(Refund.created_at)
    return list((await session.scalars(stmt)).all())


async def refunded_total(session: AsyncSession, payment_id: uuid.UUID, *, only_succeeded: bool = False) -> int:
    """failed/manual refundlar ham hisobga olinadi (ular qo'lda hal qilinadi) — ikkinchi avtomat refund yo'q."""
    stmt = select(func.coalesce(func.sum(Refund.amount_tiyin), 0)).where(Refund.payment_id == payment_id)
    if only_succeeded:
        stmt = stmt.where(Refund.status == R_SUCCEEDED)
    return int((await session.execute(stmt)).scalar_one())


async def due_refunds(session: AsyncSession, now: datetime, *, limit: int) -> list[Refund]:
    stmt = (
        select(Refund)
        .where(Refund.status == "pending", Refund.next_attempt_at <= now)
        .order_by(Refund.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def search_refunds(
    session: AsyncSession, *, status: str | None, booking_id: uuid.UUID | None, limit: int, offset: int
) -> list[Refund]:
    stmt = select(Refund)
    if status:
        stmt = stmt.where(Refund.status == status)
    if booking_id is not None:
        stmt = stmt.where(Refund.booking_id == booking_id)
    stmt = stmt.order_by(Refund.created_at.desc()).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def needs_refund_without_refund(session: AsyncSession) -> list[Payment]:
    refunded = select(Refund.payment_id)
    stmt = select(Payment).where(Payment.needs_refund.is_(True), Payment.id.not_in(refunded))
    return list((await session.scalars(stmt)).all())


# --------------------------------------------------------------------------- ledger


async def ledger_exists(session: AsyncSession, kind: str, ref_id: uuid.UUID) -> bool:
    stmt = select(LedgerTransaction.id).where(LedgerTransaction.kind == kind, LedgerTransaction.ref_id == ref_id)
    return (await session.execute(stmt)).first() is not None


async def money_taken_without_capture(session: AsyncSession) -> list[Payment]:
    captured = select(LedgerTransaction.ref_id).where(LedgerTransaction.kind == "payment_captured")
    stmt = select(Payment).where(Payment.status.in_(MONEY_TAKEN_STATUSES), Payment.id.not_in(captured))
    return list((await session.scalars(stmt)).all())


async def balances(session: AsyncSession, *, doctor_id: uuid.UUID | None = None) -> dict[str, tuple[int, int]]:
    """{account: (sum_debit, sum_credit)}"""
    stmt = select(
        LedgerEntry.account,
        func.coalesce(func.sum(LedgerEntry.debit_tiyin), 0),
        func.coalesce(func.sum(LedgerEntry.credit_tiyin), 0),
    ).group_by(LedgerEntry.account)
    if doctor_id is not None:
        stmt = stmt.where(LedgerEntry.doctor_id == doctor_id)
    return {acc: (int(d), int(c)) for acc, d, c in (await session.execute(stmt)).all()}


async def trial_balance(session: AsyncSession) -> int:
    stmt = select(
        func.coalesce(func.sum(LedgerEntry.debit_tiyin), 0) - func.coalesce(func.sum(LedgerEntry.credit_tiyin), 0)
    )
    return int((await session.execute(stmt)).scalar_one())


async def ledger_page(
    session: AsyncSession, *, booking_id: uuid.UUID | None, limit: int, offset: int
) -> list[tuple[LedgerTransaction, list[LedgerEntry]]]:
    stmt = select(LedgerTransaction)
    if booking_id is not None:
        stmt = stmt.where(LedgerTransaction.booking_id == booking_id)
    stmt = stmt.order_by(LedgerTransaction.created_at.desc(), LedgerTransaction.id).limit(limit).offset(offset)
    txns = list((await session.scalars(stmt)).all())
    entries: dict[uuid.UUID, list[LedgerEntry]] = {t.id: [] for t in txns}
    if txns:
        rows = await session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id.in_(entries)).order_by(LedgerEntry.id)
        )
        for e in rows.all():
            entries[e.transaction_id].append(e)
    return [(t, entries[t.id]) for t in txns]


# --------------------------------------------------------------------------- reconciliation


async def resolved_fingerprint_exists(session: AsyncSession, fingerprint: str) -> bool:
    stmt = select(Discrepancy.id).where(Discrepancy.fingerprint == fingerprint, Discrepancy.status == "resolved")
    return (await session.execute(stmt)).first() is not None


async def discrepancy_exists(session: AsyncSession, fingerprint: str, day: date) -> bool:
    stmt = select(Discrepancy.id).where(Discrepancy.fingerprint == fingerprint, Discrepancy.day == day)
    return (await session.execute(stmt)).first() is not None


async def discrepancy(
    session: AsyncSession, discrepancy_id: uuid.UUID, *, for_update: bool = False
) -> Discrepancy | None:
    stmt = select(Discrepancy).where(Discrepancy.id == discrepancy_id)
    if for_update:
        await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni bazadagisi bilan almashtirmasin
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).one_or_none()


async def search_discrepancies(
    session: AsyncSession, *, status: str | None, type_: str | None, limit: int, offset: int
) -> list[Discrepancy]:
    stmt = select(Discrepancy)
    if status:
        stmt = stmt.where(Discrepancy.status == status)
    if type_:
        stmt = stmt.where(Discrepancy.type == type_)
    stmt = stmt.order_by(Discrepancy.created_at.desc()).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


# --------------------------------------------------------------------------- provider logs


def add_log(
    session: AsyncSession,
    *,
    provider: str,
    direction: str,
    operation: str,
    request: dict[str, Any],
    response: dict[str, Any],
    error: str = "",
    ip: str | None = None,
    duration_ms: int = 0,
) -> None:
    session.add(
        ProviderLog(
            provider=provider,
            direction=direction,
            operation=operation[:64],
            request=request,
            response=response,
            error=error[:2000],
            ip=ip,
            duration_ms=duration_ms,
        )
    )


async def purge_logs(session: AsyncSession, before: datetime) -> int:
    result = await session.execute(delete(ProviderLog).where(ProviderLog.created_at < before))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
