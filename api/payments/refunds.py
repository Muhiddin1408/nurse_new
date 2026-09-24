"""
payments/refunds.py — refund oqimi (B4).

`request_refund` CHAQIRUVCHINING tranzaksiyasi ichida ishlaydi (bron bekor
qilish bilan bitta tranzaksiyada): Refund qatori + `RefundRequested` hodisasi.
Tarmoq chaqiruvi bu yerda YO'Q — uni `process_due_refunds` (fon ishi) qiladi.
Aks holda provayder sekin javob bersa, mijozning "bekor qilish" tugmasi osilib
qoladi va tranzaksiya qulflarni ushlab turadi.
"""

from __future__ import annotations

import logging
import random
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from api import audit
from django.db.models import Sum
from django.utils import timezone

from api.events import services as events
from api.observability import metrics
from api.payments import ledger
from api.payments.providers import ManualRefundRequired, ProviderError, get_provider
from apps.payment.models import Payment, Refund

logger = logging.getLogger(__name__)

PAYMENT_EVENTS_TOPIC = "payment.events"
MAX_REFUND_ATTEMPTS = 5
REFUND_BATCH_SIZE = 100

REFUNDABLE_STATUSES = (Payment.Status.SUCCEEDED, Payment.Status.PARTIALLY_REFUNDED)


def get_paid_amount(booking_id: UUID) -> Decimal:
    """Bron uchun hozir qo'lda turgan pul: to'langan minus qaytarilgan/qaytarilayotgan."""
    payment = _refundable_payment(booking_id)
    if payment is None:
        return Decimal("0.00")
    return payment.amount - _refunded_total(payment)


def _refundable_payment(booking_id: UUID, *, for_update: bool = False) -> Payment | None:
    qs = Payment.objects.filter(booking_id=booking_id, status__in=REFUNDABLE_STATUSES)
    if for_update:
        qs = qs.select_for_update()
    return qs.order_by("-created_at").first()


def _refunded_total(payment: Payment) -> Decimal:
    # failed refund'lar ham hisobga olinadi: ular qo'lda hal qilinadi,
    # ikkinchi marta avtomat refund yaratilmasligi kerak
    return payment.refunds.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")


def request_refund(
    *, booking_id: UUID, amount: Decimal, reason: str, initiated_by: str
) -> Refund | None:
    """Refund navbatga qo'yiladi. IDEMPOTENT: bron + sabab uchun bitta refund.

    `sum(refunds) <= payment.amount` — to'lov qatori qulflanib tekshiriladi.
    """
    if amount <= 0:
        return None

    with transaction.atomic():
        payment = _refundable_payment(booking_id, for_update=True)
        if payment is None:
            return None

        existing = Refund.objects.filter(booking_id=booking_id, reason=reason).first()
        if existing:
            return existing

        remaining = payment.amount - _refunded_total(payment)
        amount = min(Decimal(amount), remaining)
        if amount <= 0:
            return None

        try:
            with transaction.atomic():
                refund = Refund.objects.create(
                    payment=payment,
                    booking_id=booking_id,
                    amount=amount,
                    reason=reason,
                    initiated_by=initiated_by,
                    next_attempt_at=timezone.now(),
                )
        except IntegrityError:
            return Refund.objects.get(booking_id=booking_id, reason=reason)

        audit.record("refund.request", obj=refund,
                     after={"booking_id": booking_id, "amount": amount, "reason": reason, "by": initiated_by})
        events.publish(
            topic=PAYMENT_EVENTS_TOPIC,
            key=str(booking_id),
            event_type="RefundRequested",
            payload=_payload(refund),
        )
    logger.info("Refund so'raldi: booking=%s, summa=%s, sabab=%s", booking_id, amount, reason)
    return refund


def _payload(refund: Refund) -> dict:
    booking = refund.booking
    return {
        "refund_id": str(refund.id),
        "payment_id": str(refund.payment_id),
        "booking_id": str(refund.booking_id),
        "booking_number": booking.number,
        "amount": int(refund.amount),
        "reason": refund.reason,
        "status": refund.status,
        "provider": refund.payment.provider,
    }


def process_due_refunds() -> int:
    """Fon ishi: navbatdagi refund'larni provayderga yuboradi."""
    now = timezone.now()
    ids = list(
        Refund.objects.filter(status=Refund.Status.PENDING, next_attempt_at__lte=now)
        .order_by("next_attempt_at")
        .values_list("id", flat=True)[:REFUND_BATCH_SIZE]
    )
    for refund_id in ids:
        _process_one(refund_id)
    return len(ids)


def _process_one(refund_id: UUID) -> None:
    refund = Refund.objects.select_related("payment").get(id=refund_id)
    if refund.status != Refund.Status.PENDING:
        return
    provider = get_provider(refund.payment.provider)

    try:
        result = provider.refund(refund.payment, refund.amount, refund.reason)
    except ManualRefundRequired as exc:
        Refund.objects.filter(id=refund.id).update(
            status=Refund.Status.MANUAL_REQUIRED,
            last_error=str(exc)[:2000],
            updated_at=timezone.now(),
        )
        metrics.refunds_total.labels(reason=refund.reason, status="manual_required").inc()
        logger.error("ALERT: refund qo'lda bajarilishi kerak: refund=%s (%s)", refund.id, exc)
        return
    except ProviderError as exc:
        attempts = refund.attempts + 1
        if attempts >= MAX_REFUND_ATTEMPTS:
            Refund.objects.filter(id=refund.id).update(
                status=Refund.Status.FAILED, attempts=attempts,
                last_error=str(exc)[:2000], updated_at=timezone.now(),
            )
            metrics.refunds_total.labels(reason=refund.reason, status="failed").inc()
            logger.error("ALERT: refund %s marta urinishdan keyin FAILED: %s", attempts, refund.id)
        else:
            delay = timedelta(minutes=2**attempts) + timedelta(seconds=random.uniform(0, 30))
            Refund.objects.filter(id=refund.id).update(
                attempts=attempts, last_error=str(exc)[:2000],
                next_attempt_at=timezone.now() + delay, updated_at=timezone.now(),
            )
        return

    mark_refund_succeeded(refund.id, external_refund_id=result.external_refund_id)


def mark_refund_succeeded(refund_id: UUID, *, external_refund_id: str = "") -> Refund:
    """Provayder pulni qaytardi. Qo'lda (kabinetda) qilingan refund ham shu yerga keladi."""
    with transaction.atomic():
        refund = Refund.objects.select_for_update().get(id=refund_id)
        if refund.status == Refund.Status.SUCCEEDED:
            return refund
        Refund.objects.filter(id=refund.id).update(
            status=Refund.Status.SUCCEEDED,
            external_refund_id=external_refund_id,
            completed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        payment = Payment.objects.select_for_update().get(id=refund.payment_id)
        refunded = (
            payment.refunds.filter(status=Refund.Status.SUCCEEDED).aggregate(s=Sum("amount"))["s"]
            or Decimal("0")
        )
        new_status = (
            Payment.Status.REFUNDED if refunded >= payment.amount else Payment.Status.PARTIALLY_REFUNDED
        )
        Payment.objects.filter(id=payment.id).update(status=new_status, updated_at=timezone.now())
        refund.refresh_from_db()
        ledger.post_refund_succeeded(refund.id)
        events.publish(
            topic=PAYMENT_EVENTS_TOPIC,
            key=str(refund.booking_id),
            event_type="RefundSucceeded",
            payload=_payload(refund),
        )
    metrics.refunds_total.labels(reason=refund.reason, status="succeeded").inc()
    return refund
