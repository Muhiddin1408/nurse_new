"""
payments/click.py — Click SHOP API (B6).

    POST /api/v1/payment/click/prepare    action=0: to'lovni qabul qila olamizmi? (pul hali yechilmagan)
    POST /api/v1/payment/click/complete   action=1: pul yechildi (error=0) yoki bekor (error<0)

Imzo (MD5), `sign_string`:
    prepare:  md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id
                  + amount + action + sign_time)
    complete: md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id
                  + merchant_prepare_id + amount + action + sign_time)

`merchant_trans_id` — bizning `booking_id` (checkout havolasidagi `transaction_param`).
`merchant_prepare_id` — butun son bo'lishi shart; `Payment.provider_create_time` (ms).

Payme kabi QOIDA: bu yerdan istisno chiqib 500 bo'lmaydi — har doim JSON + `error`.
Umumiy "pul yechildi" logikasi — `settlement.settle_success` (Payme bilan bitta).

⚠️ Kodlar va maydonlarni joriy rasmiy Click SHOP API hujjati bilan solishtiring.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.observability import metrics
from api.payments import settlement
from api.schedule import services as schedule_services
from apps.booking.models import Booking
from apps.payment.models import Payment

logger = logging.getLogger(__name__)

ACTION_PREPARE = 0
ACTION_COMPLETE = 1

# Click xato kodlari
OK = 0
ERR_SIGN = -1
ERR_AMOUNT = -2
ERR_ACTION = -3
ERR_ALREADY_PAID = -4
ERR_ORDER_NOT_FOUND = -5
ERR_TRANSACTION_NOT_FOUND = -6
ERR_UPDATE_FAILED = -7
ERR_BAD_REQUEST = -8
ERR_CANCELLED = -9

REQUIRED = ("click_trans_id", "service_id", "merchant_trans_id", "amount", "action", "sign_time", "sign_string")

PAYMENT_HOLD_EXTENSION_MINUTES = 15


class ClickError(Exception):
    def __init__(self, code: int, note: str):
        super().__init__(note)
        self.code = code
        self.note = note


def _now_ms() -> int:
    return int(time.time() * 1000)


def expected_sign(p: dict, *, with_prepare_id: bool) -> str:
    parts = [
        str(p["click_trans_id"]), str(p["service_id"]), settings.CLICK_SECRET_KEY,
        str(p["merchant_trans_id"]),
    ]
    if with_prepare_id:
        parts.append(str(p.get("merchant_prepare_id", "")))
    parts += [str(p["amount"]), str(p["action"]), str(p["sign_time"])]
    return hashlib.md5("".join(parts).encode()).hexdigest()  # nosec B324 — Click protokoli MD5 talab qiladi


def _verify(p: dict, *, with_prepare_id: bool) -> None:
    missing = [k for k in REQUIRED if k not in p]
    if missing or (with_prepare_id and "merchant_prepare_id" not in p):
        raise ClickError(ERR_BAD_REQUEST, "So'rov to'liq emas")
    # Kalit bo'sh bo'lsa — fail-closed (Payme bilan bir xil qoida)
    if not settings.CLICK_SECRET_KEY:
        raise ClickError(ERR_SIGN, "Imzo tekshiruvi sozlanmagan")
    if not hmac.compare_digest(expected_sign(p, with_prepare_id=with_prepare_id), str(p["sign_string"])):
        raise ClickError(ERR_SIGN, "Imzo noto'g'ri")
    if str(p["service_id"]) != str(settings.CLICK_SERVICE_ID):
        raise ClickError(ERR_BAD_REQUEST, "service_id noto'g'ri")


def _amount(p: dict) -> Decimal:
    try:
        return Decimal(str(p["amount"])).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ClickError(ERR_AMOUNT, "Summa noto'g'ri") from None


def _booking(p: dict) -> Booking:
    try:
        booking_id = UUID(str(p["merchant_trans_id"]))
    except ValueError:
        raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma topilmadi") from None
    booking = Booking.objects.filter(id=booking_id).first()
    if booking is None:
        raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma topilmadi")
    return booking


def handle(action: int, params: dict) -> dict:
    """Javob — Click kutgan to'liq JSON. Hech qachon istisno chiqarmaydi."""
    base = {
        "click_trans_id": params.get("click_trans_id"),
        "merchant_trans_id": params.get("merchant_trans_id"),
    }
    try:
        if str(params.get("action")) != str(action):
            raise ClickError(ERR_ACTION, "action noto'g'ri")
        result = _prepare(params) if action == ACTION_PREPARE else _complete(params)
        outcome = "ok"
        return {**base, **result, "error": OK, "error_note": "Success"}
    except ClickError as exc:
        outcome = "error"
        metrics.provider_errors.labels(provider="click", code=str(exc.code)).inc()
        return {**base, "error": exc.code, "error_note": exc.note}
    except Exception:  # B3: 500 emas — Click qayta yuboradi, pul holati noaniq qoladi
        outcome = "exception"
        logger.exception("Click webhook kutilmagan xato: action=%s", action)
        return {**base, "error": ERR_UPDATE_FAILED, "error_note": "Ichki xato"}
    finally:
        metrics.payment_webhook_received.labels(
            method="click_prepare" if action == ACTION_PREPARE else "click_complete", result=outcome
        ).inc()


def _prepare(p: dict) -> dict:
    _verify(p, with_prepare_id=False)
    booking = _booking(p)
    amount = _amount(p)

    with transaction.atomic():
        existing = Payment.objects.select_for_update().filter(
            provider=Payment.Provider.CLICK, external_id=str(p["click_trans_id"])
        ).first()
        if existing is not None:  # Click Prepare'ni qayta yubordi — idempotent
            if existing.status == Payment.Status.CANCELLED:
                raise ClickError(ERR_CANCELLED, "Tranzaksiya bekor qilingan")
            return {"merchant_prepare_id": existing.provider_create_time}

        if Payment.objects.filter(booking=booking, status=Payment.Status.SUCCEEDED).exists():
            raise ClickError(ERR_ALREADY_PAID, "Buyurtma allaqachon to'langan")
        # B3: to'lab bo'lmaydigan bron uchun pul YECHILMAYDI
        if booking.status != Booking.Status.PENDING_PAYMENT or not schedule_services.is_hold_active(booking.slot_id):
            raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma muddati o'tgan yoki bekor qilingan")

        payment = (
            Payment.objects.select_for_update()
            .filter(booking=booking, provider=Payment.Provider.CLICK, status=Payment.Status.CREATED)
            .order_by("-created_at")
            .first()
        )
        if payment is None:
            raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma uchun to'lov ochilmagan")
        if payment.amount != amount:
            raise ClickError(ERR_AMOUNT, "Summa mos emas")

        prepare_id = _now_ms()
        Payment.objects.filter(id=payment.id).update(
            status=Payment.Status.PROCESSING,
            external_id=str(p["click_trans_id"]),
            provider_create_time=prepare_id,
            provider_state={**(payment.provider_state or {}), "click_paydoc_id": p.get("click_paydoc_id")},
            updated_at=timezone.now(),
        )
        # Mijoz Click ilovasida to'layapti — hold'ni uzaytiramiz (Payme bilan bir xil)
        schedule_services.extend_hold(booking.slot_id, PAYMENT_HOLD_EXTENSION_MINUTES)
    return {"merchant_prepare_id": prepare_id}


def _complete(p: dict) -> dict:
    _verify(p, with_prepare_id=True)
    amount = _amount(p)

    cancelled_by_click = False
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(
            provider=Payment.Provider.CLICK, external_id=str(p["click_trans_id"])
        ).first()
        if payment is None or str(payment.provider_create_time) != str(p["merchant_prepare_id"]):
            raise ClickError(ERR_TRANSACTION_NOT_FOUND, "Tranzaksiya topilmadi")
        if payment.amount != amount:
            raise ClickError(ERR_AMOUNT, "Summa mos emas")

        if payment.status == Payment.Status.SUCCEEDED:  # takroriy Complete — idempotent
            return {"merchant_confirm_id": payment.perform_time}
        if payment.status == Payment.Status.CANCELLED:
            raise ClickError(ERR_CANCELLED, "Tranzaksiya bekor qilingan")

        # Click to'lovni o'zi bekor qildi (mablag' yetmadi va h.k.) — pul yechilmagan
        if int(p.get("error", 0)) < 0:
            Payment.objects.filter(id=payment.id).update(
                status=Payment.Status.CANCELLED,
                cancel_time=_now_ms(),
                provider_state={**(payment.provider_state or {}), "click_error": p.get("error"),
                                "click_error_note": p.get("error_note", "")},
                updated_at=timezone.now(),
            )
            # Xato atomic'dan TASHQARIDA ko'tariladi — aks holda bekor qilish rollback bo'lardi
            cancelled_by_click = True
        else:
            confirm_id = _now_ms()
            try:
                settlement.settle_success(payment, perform_time=confirm_id)
            except settlement.AlreadyPaidElsewhere:
                raise ClickError(ERR_ALREADY_PAID, "Buyurtma allaqachon to'langan") from None

    if cancelled_by_click:
        raise ClickError(ERR_CANCELLED, "Tranzaksiya bekor qilingan")

    payment.refresh_from_db()
    settlement.observe_success(payment)
    return {"merchant_confirm_id": confirm_id}


__all__ = ["handle", "expected_sign", "ACTION_PREPARE", "ACTION_COMPLETE"]
