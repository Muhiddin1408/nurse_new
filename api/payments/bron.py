from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction

from api.observability import metrics
from api.payments.services import PaymentError
from api.schedule import services as schedule_services
from apps.booking.models import Booking
from apps.payment.models import Payment


class CheckoutNotAllowed(PaymentError):
    """Bron to'lanadigan holatda emas -> 409."""


@dataclass(frozen=True)
class CheckoutDTO:
    payment_id: UUID
    checkout_url: str
    amount: int
    expires_at: datetime | None


def create_checkout(*, booking: Booking, provider: str) -> CheckoutDTO:
    """B1: bron uchun to'lov urinishini ochadi va provayder havolasini qaytaradi.

    IDEMPOTENT: shu provayder uchun ochiq (`created`/`processing`) Payment bo'lsa,
    yangisi yaratilmaydi — o'sha qaytariladi. Bitta bronga bir nechta urinish
    bo'lishi mumkin (Payme bekor bo'ldi -> Click), lekin faqat bittasi
    `succeeded` bo'la oladi (model constraint'i).
    """
    if provider not in (Payment.Provider.PAYME, Payment.Provider.CLICK):
        raise PaymentError(f"Noma'lum provayder: {provider}")

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(id=booking.id)
        if booking.status != Booking.Status.PENDING_PAYMENT:
            raise CheckoutNotAllowed("Bron to'lov kutish holatida emas")
        # B10: klinikada to'lanadigan bronni onlayn to'lab bo'lmaydi —
        # u allaqachon tasdiqlangan va bu yerga umuman kelmasligi kerak
        if booking.prepay_amount <= 0:
            raise CheckoutNotAllowed("Bu bron klinikada to'lanadi")
        if not schedule_services.is_hold_active(booking.slot_id):
            raise CheckoutNotAllowed("To'lov muddati o'tgan, vaqtni qaytadan tanlang")

        payment = (
            Payment.objects.filter(
                booking=booking,
                provider=provider,
                status__in=[Payment.Status.CREATED, Payment.Status.PROCESSING],
            )
            .order_by("-created_at")
            .first()
        )
        if payment is None:
            payment = Payment.objects.create(
                booking=booking,
                provider=provider,
                # B10: onlayn to'lanadigan summa — `total_price` emas.
                # Zakladda qolgan qismi klinikada to'lanadi.
                amount=booking.prepay_amount,
                status=Payment.Status.CREATED,
                idempotency_key=f"checkout-{uuid4()}",
            )
            metrics.payment_checkout_created.labels(provider=provider).inc()

    slot = schedule_services.get_slot_for_booking(booking.slot_id)
    return CheckoutDTO(
        payment_id=payment.id,
        checkout_url=build_checkout_url(payment),
        amount=int(payment.amount),
        expires_at=slot.hold_expires_at if slot else None,
    )


def build_checkout_url(payment: Payment) -> str:
    """Payme/Click uchun to'lov havolasi.

    Payme: `https://checkout.paycom.uz/<base64("m=..;ac.booking_id=..;a=<tiyin>")>`
    """
    if payment.provider == Payment.Provider.PAYME:
        params = (
            f"m={settings.PAYME_MERCHANT_ID};"
            f"ac.booking_id={payment.booking_id};"
            f"a={int(payment.amount * 100)}"
        )
        encoded = base64.b64encode(params.encode()).decode()
        return f"{settings.PAYME_CHECKOUT_URL}/{encoded}"
    if payment.provider == Payment.Provider.CLICK:
        return (
            f"https://my.click.uz/services/pay?service_id={settings.CLICK_SERVICE_ID}"
            f"&merchant_id={settings.CLICK_MERCHANT_ID}"
            f"&amount={int(payment.amount)}&transaction_param={payment.booking_id}"
        )
    raise PaymentError(f"Noma'lum provayder: {payment.provider}")
