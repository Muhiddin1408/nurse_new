"""Ikki yozuvli daftar (B8): har pul harakati ≥ 2 qator, sum(debit) == sum(credit). Istalgan paytda
"shifokorlarga qancha qarzdormiz" savoliga aniq javob. Taqsimot — `medbron_common.money.share_of` (billing
bilan YAGONA formula, ADR-0012).

    To'lov yechildi (A, bron money_split'i bo'yicha):
        Dr client_payments  A
            Cr doctor_payable     doctor ulushi
            Cr platform_revenue   platforma ulushi (chegirmani platforma ko'tarsa — manfiy → Dr tomonga o'tadi)
            Cr provider_fees      provayder ulushi

    Refund / provayder qaytarishi (R, shifokor ulushi D — event'dagi `doctor_share_tiyin`):
        Dr doctor_payable     D
        Dr platform_revenue   R − D   (provayder komissiyasi qaytmaydi — uni platforma ko'taradi)
            Cr client_payments    R

    Payout to'landi (billing `payout.paid`, N):
        Dr doctor_payable     N
            Cr client_payments    N
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus.envelope import new_event_id
from medbron_common.money import share_of

from app.models import LedgerEntry, LedgerTransaction, Payment, Refund
from app.models.payment import CLIENT_PAYMENTS, DOCTOR_PAYABLE, PLATFORM_REVENUE, PROVIDER_FEES
from app.repositories import payments as repo

Line = tuple[str, int, int]  # (hisob, debit, credit)


class LedgerImbalance(Exception):
    pass


def _normalize(account: str, debit: int, credit: int) -> Line:
    """Manfiy summa teskari tomonga o'tadi (platforma ko'targan chegirma — daromad hisobiga DEBET)."""
    if debit < 0:
        debit, credit = 0, credit - debit
    if credit < 0:
        debit, credit = debit - credit, 0
    return account, debit, credit


async def post(
    session: AsyncSession,
    *,
    kind: str,
    ref_type: str,
    ref_id: uuid.UUID,
    booking_id: uuid.UUID | None,
    doctor_id: uuid.UUID | None,
    lines: list[Line],
    description: str = "",
) -> LedgerTransaction | None:
    """IDEMPOTENT: (kind, ref_id) ikkinchi marta yozilmaydi (None). Chaqiruvchining tranzaksiyasi ichida."""
    normalized = [_normalize(*line) for line in lines]
    normalized = [line for line in normalized if line[1] or line[2]]
    debit = sum(d for _, d, _ in normalized)
    credit = sum(c for _, _, c in normalized)
    if debit != credit:
        raise LedgerImbalance(f"{kind}: debit {debit} != credit {credit}")
    if await repo.ledger_exists(session, kind, ref_id):
        return None
    txn = LedgerTransaction(
        id=new_event_id(), kind=kind, ref_type=ref_type, ref_id=ref_id, booking_id=booking_id, description=description
    )
    try:
        async with session.begin_nested():
            session.add(txn)
            await session.flush()
            session.add_all(
                LedgerEntry(
                    transaction_id=txn.id,
                    account=acc,
                    debit_tiyin=d,
                    credit_tiyin=c,
                    doctor_id=doctor_id if acc == DOCTOR_PAYABLE else None,
                )
                for acc, d, c in normalized
            )
            await session.flush()
    except IntegrityError:
        return None  # parallel yozildi — idempotent
    return txn


async def post_capture(session: AsyncSession, p: Payment) -> LedgerTransaction | None:
    share = share_of(p.money, p.amount_tiyin)
    return await post(
        session,
        kind="payment_captured",
        ref_type="payment",
        ref_id=p.id,
        booking_id=p.booking_id,
        doctor_id=p.doctor_id,
        description=f"To'lov {p.booking_number} ({p.provider})",
        lines=[
            (CLIENT_PAYMENTS, p.amount_tiyin, 0),
            (DOCTOR_PAYABLE, 0, share.doctor_tiyin),
            (PLATFORM_REVENUE, 0, share.platform_tiyin),
            (PROVIDER_FEES, 0, share.provider_tiyin),
        ],
    )


def _return_lines(amount_tiyin: int, doctor_share_tiyin: int) -> list[Line]:
    return [
        (DOCTOR_PAYABLE, doctor_share_tiyin, 0),
        (PLATFORM_REVENUE, amount_tiyin - doctor_share_tiyin, 0),
        (CLIENT_PAYMENTS, 0, amount_tiyin),
    ]


async def post_refund(session: AsyncSession, r: Refund, p: Payment) -> LedgerTransaction | None:
    return await post(
        session,
        kind="refund_succeeded",
        ref_type="refund",
        ref_id=r.id,
        booking_id=r.booking_id,
        doctor_id=p.doctor_id,
        description=f"Refund {p.booking_number} ({r.source})",
        lines=_return_lines(r.amount_tiyin, r.doctor_share_tiyin),
    )


async def post_reversal(
    session: AsyncSession, p: Payment, *, amount_tiyin: int, doctor_share_tiyin: int
) -> LedgerTransaction | None:
    return await post(
        session,
        kind="payment_reversed",
        ref_type="payment",
        ref_id=p.id,
        booking_id=p.booking_id,
        doctor_id=p.doctor_id,
        description=f"Provayder qaytardi {p.booking_number} ({p.provider})",
        lines=_return_lines(amount_tiyin, doctor_share_tiyin),
    )


async def post_payout_paid(session: AsyncSession, data: dict[str, Any]) -> LedgerTransaction | None:
    net = int(data["net_tiyin"])
    return await post(
        session,
        kind="payout_paid",
        ref_type="payout",
        ref_id=uuid.UUID(data["payout_id"]),
        booking_id=None,
        doctor_id=uuid.UUID(data["doctor_id"]),
        description=f"Payout {data['bank_reference']}"[:255],
        lines=[(DOCTOR_PAYABLE, net, 0), (CLIENT_PAYMENTS, 0, net)],
    )


def balance_of(account: str, debit: int, credit: int) -> int:
    """Aktiv (client_payments): debit − credit; qolganlari (passiv/daromad): credit − debit."""
    return debit - credit if account == CLIENT_PAYMENTS else credit - debit


async def balances(session: AsyncSession, *, doctor_id: uuid.UUID | None = None) -> dict[str, int]:
    sums = await repo.balances(session, doctor_id=doctor_id)
    accounts = (DOCTOR_PAYABLE,) if doctor_id else (CLIENT_PAYMENTS, PLATFORM_REVENUE, DOCTOR_PAYABLE, PROVIDER_FEES)
    return {acc: balance_of(acc, *sums.get(acc, (0, 0))) for acc in accounts}
