"""
payments/ledger.py — ikki yozuvli daftar (B8).

Har bir pul harakati kamida ikki qator: sum(debit) == sum(credit). Shunda
istalgan paytda "shifokorlarga qancha qarzdormiz" savoliga aniq javob bor.

Yozuv qoidalari:

    To'lov o'tdi (A = to'langan summa, bron snapshot'i bo'yicha taqsimlanadi):
        Dr client_payments    A
            Cr doctor_payable      doctor_payout
            Cr platform_revenue    platform_fee
            Cr provider_fees       provider_fee

    Refund o'tdi (R):
        Dr platform_revenue   R * commission_rate
        Dr doctor_payable     R - platform ulushi
            Cr client_payments     R
        Provayder komissiyasi qaytmaydi (provayder uni ushlab qoladi).

Kechikib bekor qilishdagi jarima alohida yozuvsiz ishlaydi: qaytmagan qism
to'lov paytida shifokor va platforma o'rtasida allaqachon taqsimlangan.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum

from api.booking.pricing import split_amount
from apps.booking.models import Booking
from apps.payment.models import LedgerEntry, LedgerTransaction, Payment, Refund

A = LedgerEntry.Account
ZERO = Decimal("0.00")


class LedgerImbalance(Exception):
    pass


def _normalize(d: Decimal, c: Decimal) -> tuple[Decimal, Decimal]:
    if d < 0:
        d, c = ZERO, c - d
    if c < 0:
        d, c = d - c, ZERO
    return d, c


def _post(*, kind, ref_type, ref_id, booking: Booking | None, lines, description="", doctor_id=None) -> LedgerTransaction | None:
    # Manfiy summa teskari tomonga o'tadi (C12: platforma to'lagan chegirma —
    # daromad hisobiga DEBET, ya'ni marketing xarajati)
    lines = [(acc, *_normalize(Decimal(d), Decimal(c))) for acc, d, c in lines]
    lines = [(acc, d, c) for acc, d, c in lines if d or c]
    debit = sum(d for _, d, _ in lines)
    credit = sum(c for _, _, c in lines)
    if debit != credit:
        raise LedgerImbalance(f"{kind}: debit {debit} != credit {credit}")

    if LedgerTransaction.objects.filter(kind=kind, ref_id=ref_id).exists():
        return None
    try:
        with transaction.atomic():
            txn = LedgerTransaction.objects.create(
                kind=kind, ref_type=ref_type, ref_id=ref_id, booking=booking, description=description
            )
            LedgerEntry.objects.bulk_create(
                LedgerEntry(
                    transaction=txn, account=acc, debit=d, credit=c,
                    doctor_id=(doctor_id or (booking.doctor_id if booking else None))
                    if acc == A.DOCTOR_PAYABLE else None,
                )
                for acc, d, c in lines
            )
    except IntegrityError:
        return None  # parallel yozildi — idempotent
    return txn


def post_payment_succeeded(payment_id: UUID) -> LedgerTransaction | None:
    payment = Payment.objects.select_related("booking").get(id=payment_id)
    b = payment.booking
    if payment.amount == b.total_price and b.platform_fee + b.provider_fee + b.doctor_payout == b.total_price:
        platform_fee, provider_fee, doctor_payout = b.platform_fee, b.provider_fee, b.doctor_payout
    else:
        s = split_amount(payment.amount, b.commission_rate, b.provider_fee_rate)
        platform_fee, provider_fee, doctor_payout = s.platform_fee, s.provider_fee, s.doctor_payout

    return _post(
        kind="payment_succeeded", ref_type="payment", ref_id=payment.id, booking=b,
        description=f"To'lov {b.number}",
        lines=[
            (A.CLIENT_PAYMENTS, payment.amount, ZERO),
            (A.DOCTOR_PAYABLE, ZERO, doctor_payout),
            (A.PLATFORM_REVENUE, ZERO, platform_fee),
            (A.PROVIDER_FEES, ZERO, provider_fee),
        ],
    )


def post_refund_succeeded(refund_id: UUID) -> LedgerTransaction | None:
    refund = Refund.objects.select_related("booking").get(id=refund_id)
    b = refund.booking
    platform_part = (refund.amount * b.commission_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return _post(
        kind="refund_succeeded", ref_type="refund", ref_id=refund.id, booking=b,
        description=f"Refund {b.number} ({refund.reason})",
        lines=[
            (A.PLATFORM_REVENUE, platform_part, ZERO),
            (A.DOCTOR_PAYABLE, refund.amount - platform_part, ZERO),
            (A.CLIENT_PAYMENTS, ZERO, refund.amount),
        ],
    )


def post_payout_paid(payout) -> LedgerTransaction | None:
    """Shifokorga bank orqali to'landi: qarz kamayadi, pul platformadan chiqadi.

        Dr doctor_payable    net
            Cr client_payments    net
    """
    return _post(
        kind="payout_paid", ref_type="payout", ref_id=payout.id, booking=None, doctor_id=payout.doctor_id,
        description=f"Payout {payout.period_start}–{payout.period_end}",
        lines=[
            (A.DOCTOR_PAYABLE, payout.net_amount, ZERO),
            (A.CLIENT_PAYMENTS, ZERO, payout.net_amount),
        ],
    )


def balance(account: str, *, doctor_id: UUID | None = None) -> Decimal:
    """Hisob qoldig'i. Aktiv (client_payments): debit - credit; qolganlari credit - debit."""
    qs = LedgerEntry.objects.filter(account=account)
    if doctor_id is not None:
        qs = qs.filter(doctor_id=doctor_id)
    s = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    d, c = s["d"] or ZERO, s["c"] or ZERO
    return d - c if account == A.CLIENT_PAYMENTS else c - d


def trial_balance() -> Decimal:
    """Butun daftar bo'yicha sum(debit) - sum(credit). Har doim 0 bo'lishi shart."""
    s = LedgerEntry.objects.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (s["d"] or ZERO) - (s["c"] or ZERO)
