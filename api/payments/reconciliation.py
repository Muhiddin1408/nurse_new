"""
payments/reconciliation.py — kunlik solishtirish (B7).

Ikki qism:

1. TASHQI: provayder tranzaksiyalari (statement API yoki kabinetdan olingan
   reestr fayli) <-> bizning `Payment` qatorlari.
       provider_only    🔴 pul olindi, xizmat yo'q — ENG XAVFLI
       local_only       🔴 xizmat berildi, pul yo'q
       amount_mismatch  🔴
       status_mismatch  🟠

2. ICHKI (har doim ishlaydi, provayderga bog'liq emas):
       missing_ledger          — succeeded to'lov daftarga tushmagan
       booking_without_payment — confirmed/completed bron, lekin to'lov yo'q
       unresolved_refund       — needs_refund, lekin Refund yaratilmagan
       ledger_imbalance        — sum(debit) != sum(credit)

Maqsad: farqlar soni doimiy 0. Har bir farq -> ALERT + admin navbati.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import IntegrityError, transaction

from api.observability import metrics
from api.payments import ledger
from api.payments.providers import TransactionDTO
from apps.booking.models import Booking
from apps.payment.models import LedgerTransaction, Payment, PaymentDiscrepancy, Refund

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Tashkent")
T = PaymentDiscrepancy.Type

# Payme state -> bizning Payment holatlari
PAYME_STATE_TO_STATUSES = {
    1: {Payment.Status.PROCESSING},
    2: {Payment.Status.SUCCEEDED},
    -1: {Payment.Status.CANCELLED, Payment.Status.EXPIRED},
    -2: {Payment.Status.REFUNDED, Payment.Status.PARTIALLY_REFUNDED},
}
MONEY_TAKEN_STATES = {2, -2}
MONEY_TAKEN_STATUSES = {Payment.Status.SUCCEEDED, Payment.Status.REFUNDED, Payment.Status.PARTIALLY_REFUNDED}


def day_window_ms(day: date) -> tuple[int, int]:
    start = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1


def _record(day: date, type_: str, **fields) -> PaymentDiscrepancy | None:
    payment = fields.get("payment")
    fingerprint = ":".join(str(x or "") for x in (
        type_, fields.get("provider"), fields.get("external_id"),
        payment.id if payment else "", fields.get("booking_id") or getattr(fields.get("booking"), "id", ""),
    ))
    if type_ != T.LEDGER_IMBALANCE and PaymentDiscrepancy.objects.filter(
        fingerprint=fingerprint, status=PaymentDiscrepancy.Status.RESOLVED
    ).exists():
        return None  # admin allaqachon ko'rib chiqib hal qilgan
    try:
        with transaction.atomic():
            d = PaymentDiscrepancy.objects.create(
                type=type_, period_date=day, fingerprint=fingerprint, **fields
            )
    except IntegrityError:
        return None  # shu davr uchun allaqachon yozilgan
    metrics.reconciliation_discrepancies.labels(type=type_).inc()
    logger.error("ALERT: to'lov farqi %s: %s", type_, fields)
    return d


def reconcile_provider(provider: str, day: date, provider_txs: list[TransactionDTO]) -> list[PaymentDiscrepancy]:
    frm, to = day_window_ms(day)
    local = {
        p.external_id: p
        for p in Payment.objects.filter(
            provider=provider, provider_create_time__gte=frm, provider_create_time__lte=to
        ).exclude(external_id="")
    }
    found: list[PaymentDiscrepancy | None] = []
    seen = set()

    for tx in provider_txs:
        seen.add(tx.external_id)
        p = local.get(tx.external_id) or Payment.objects.filter(
            provider=provider, external_id=tx.external_id
        ).first()

        if p is None:
            if tx.state in MONEY_TAKEN_STATES:
                found.append(_record(day, T.PROVIDER_ONLY, provider=provider, external_id=tx.external_id,
                                     provider_amount=tx.amount,
                                     details={"state": tx.state, "booking_id": tx.booking_id}))
            continue

        if p.amount != tx.amount:
            found.append(_record(day, T.AMOUNT_MISMATCH, provider=provider, external_id=tx.external_id,
                                 payment=p, booking_id=p.booking_id,
                                 local_amount=p.amount, provider_amount=tx.amount))
        if p.status not in PAYME_STATE_TO_STATUSES.get(tx.state, set()):
            found.append(_record(day, T.STATUS_MISMATCH, provider=provider, external_id=tx.external_id,
                                 payment=p, booking_id=p.booking_id,
                                 details={"local_status": p.status, "provider_state": tx.state}))

    for external_id, p in local.items():
        if external_id not in seen and p.status in MONEY_TAKEN_STATUSES:
            found.append(_record(day, T.LOCAL_ONLY, provider=provider, external_id=external_id,
                                 payment=p, booking_id=p.booking_id, local_amount=p.amount))

    return [d for d in found if d]


def reconcile_internal(day: date) -> list[PaymentDiscrepancy]:
    found: list[PaymentDiscrepancy | None] = []

    ledgered = LedgerTransaction.objects.filter(kind="payment_succeeded").values_list("ref_id", flat=True)
    for p in Payment.objects.filter(status__in=MONEY_TAKEN_STATUSES).exclude(id__in=ledgered):
        found.append(_record(day, T.MISSING_LEDGER, provider=p.provider, external_id=p.external_id,
                             payment=p, booking_id=p.booking_id, local_amount=p.amount))

    paid_bookings = Payment.objects.filter(status__in=MONEY_TAKEN_STATUSES).values_list("booking_id", flat=True)
    for b in Booking.objects.filter(
        status__in=[Booking.Status.CONFIRMED, Booking.Status.COMPLETED]
    ).exclude(id__in=paid_bookings):
        found.append(_record(day, T.BOOKING_WITHOUT_PAYMENT, booking=b, local_amount=b.total_price))

    refunded_bookings = Refund.objects.values_list("booking_id", flat=True)
    for p in Payment.objects.filter(needs_refund=True).exclude(booking_id__in=refunded_bookings):
        found.append(_record(day, T.UNRESOLVED_REFUND, provider=p.provider, external_id=p.external_id,
                             payment=p, booking_id=p.booking_id, local_amount=p.amount))

    imbalance = ledger.trial_balance()
    if imbalance != 0:
        found.append(_record(day, T.LEDGER_IMBALANCE, details={"imbalance": str(imbalance)}))

    return [d for d in found if d]


def parse_payme_registry(path: str) -> list[TransactionDTO]:
    """Payme kabinetidan olingan reestr (CSV).

    Kutilgan ustunlar: id, amount (tiyin), state, booking_id, create_time,
    perform_time, cancel_time. ⚠️ Haqiqiy reestr formatini Payme bilan
    tasdiqlang va kerak bo'lsa shu parserni moslang.
    """
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(TransactionDTO(
                external_id=r["id"].strip(),
                amount=Decimal(r["amount"]) / 100,
                state=int(r["state"]),
                booking_id=r.get("booking_id", "").strip(),
                create_time=int(r.get("create_time") or 0),
                perform_time=int(r.get("perform_time") or 0),
                cancel_time=int(r.get("cancel_time") or 0),
            ))
    return rows
