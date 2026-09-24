"""
payments/settlement.py — "pul yechildi" hodisasining provayderdan mustaqil qismi (B6).

Payme `PerformTransaction` va Click `Complete` aynan bir xil ishni qiladi:
bronni tasdiqlash, to'lovni `succeeded` qilish, daftarga yozish, kerak bo'lsa
refund. Bu qoidalar ikki joyda yashasa, birinchi tuzatish ikkinchisini
unutadi — shuning uchun bitta funksiya.

Chaqiruvchi `transaction.atomic()` ichida va to'lov qatori QULFLANGAN holda chaqiradi.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from api.booking import services as booking_services
from api.observability import metrics
from api.payments import ledger
from api.payments import refunds as refund_services
from api.schedule import services as schedule_services
from apps.payment.models import Payment

logger = logging.getLogger(__name__)


class AlreadyPaidElsewhere(Exception):
    """Bron boshqa to'lov bilan allaqachon to'langan — ikkinchi marta yechilmaydi."""


def settle_success(payment: Payment, *, perform_time: int, extra_state: dict | None = None) -> bool:
    """Qaytaradi: `needs_refund` (B3 — pul yechildi, bron tasdiqlanmadi).

    FAIL-SAFE: bronni tasdiqlab bo'lmasa ham (poyga holati) pul allaqachon
    yechilgan — haqiqatni yozamiz (`succeeded` + `needs_refund`), avtomat
    refund navbatga qo'yiladi va provayderga MUVAFFAQIYAT qaytariladi.
    """
    if Payment.objects.filter(booking_id=payment.booking_id, status=Payment.Status.SUCCEEDED).exists():
        raise AlreadyPaidElsewhere("Buyurtma allaqachon to'langan")

    needs_refund = False
    try:
        with transaction.atomic():
            booking_services.confirm_booking(payment.booking_id)
    except (booking_services.BookingError, schedule_services.SlotNotAvailable) as exc:
        needs_refund = True
        logger.error(
            "ALERT: pul yechildi, lekin bron tasdiqlanmadi — refund kerak: payment=%s, xato=%s",
            payment.id, exc,
        )
        metrics.payment_refund_required.labels(provider=payment.provider).inc()

    fields = dict(
        status=Payment.Status.SUCCEEDED,
        perform_time=perform_time,
        needs_refund=needs_refund,
        updated_at=timezone.now(),
    )
    if extra_state:
        fields["provider_state"] = {**(payment.provider_state or {}), **extra_state}
    Payment.objects.filter(id=payment.id).update(**fields)

    # B8: pul harakati daftarga (to'lov bilan BITTA tranzaksiyada)
    ledger.post_payment_succeeded(payment.id)
    if needs_refund:
        refund_services.request_refund(
            booking_id=payment.booking_id,
            amount=payment.amount,
            reason="booking_unavailable",
            initiated_by="system",
        )
    return needs_refund


def observe_success(payment: Payment) -> None:
    """Commit'dan KEYIN chaqiriladi (rollback bo'lsa metrika yolg'on bo'lmasin)."""
    metrics.payment_succeeded.labels(provider=payment.provider).inc()
    metrics.payment_duration.labels(provider=payment.provider).observe(
        max(0.0, (payment.updated_at - payment.created_at).total_seconds())
    )
