"""
payments/webhook.py — Payme Merchant API (JSON-RPC), to'liq metodlar to'plami (B2).

    CheckPerformTransaction  — to'lovdan OLDIN: qabul qila olamizmi?
    CreateTransaction        — tranzaksiya ochildi (pul hali yechilmagan)
    PerformTransaction       — pul yechildi -> bron tasdiqlanadi
    CancelTransaction        — bekor (state 1 -> -1) yoki qaytarish (state 2 -> -2)
    CheckTransaction         — holat so'rovi
    GetStatement             — davr bo'yicha tranzaksiyalar (reconciliation)
    SetFiscalData            — fiskal chek ma'lumoti

Tranzaksiya holatlari: 1 yaratilgan, 2 bajarilgan, -1 yaratilgandan bekor,
-2 bajarilgandan bekor.

QOIDA: bu yerdan HECH QACHON istisno chiqib 500 bo'lmasligi kerak (B3).
Payme 500 ni "javob bermadi" deb qayta yuboradi — pul esa allaqachon
yechilgan bo'lishi mumkin. Har doim 200 + `error` maydoni.

⚠️ Aniq kodlar va maydonlarni joriy rasmiy Payme Merchant API hujjatiga
solishtirib tekshiring — protokol vaqt o'tishi bilan o'zgargan.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from api.booking import services as booking_services
from api.observability import metrics
from api.payments import fiscal, settlement
from api.payments import refunds as refund_services
from api.payments.services import PaymentError
from api.schedule import services as schedule_services
from apps.booking.models import Booking
from apps.payment.models import Payment, Refund

logger = logging.getLogger(__name__)

# Payme tranzaksiyasi 12 soatda eskiradi
TRANSACTION_TIMEOUT_MS = 12 * 60 * 60 * 1000

# B3: mijoz checkout sahifasida to'layotgan bo'lsa, hold shu qadar uzaytiriladi
PAYMENT_HOLD_EXTENSION_MINUTES = 15

STATE_CREATED = 1
STATE_PERFORMED = 2
STATE_CANCELLED = -1
STATE_CANCELLED_AFTER_PERFORM = -2

CANCEL_REASON_TIMEOUT = 4

# Xato kodlari
ERR_INVALID_AMOUNT = -31001
ERR_TRANSACTION_NOT_FOUND = -31003
ERR_CANNOT_CANCEL = -31007
ERR_CANNOT_PERFORM = -31008
ERR_ACCOUNT_NOT_FOUND = -31050
ERR_BOOKING_UNAVAILABLE = -31099
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601

ALLOWED_ACCOUNT_FIELDS = {"booking_id"}


class PaymeError(Exception):
    def __init__(self, code: int, message: str, data: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def as_response(self) -> dict:
        error = {
            "code": self.code,
            "message": {"uz": self.message, "ru": self.message, "en": self.message},
        }
        if self.data:
            error["data"] = self.data
        return {"error": error}


def _now_ms() -> int:
    return int(timezone.now().timestamp() * 1000)


def handle_payme_webhook(*, method: str, params: dict) -> dict:
    handlers = {
        "CheckPerformTransaction": _check_perform,
        "CreateTransaction": _create,
        "PerformTransaction": _perform,
        "CancelTransaction": _cancel,
        "CheckTransaction": _check,
        "GetStatement": _get_statement,
        "SetFiscalData": _set_fiscal_data,
    }
    handler = handlers.get(method)
    if handler is None:
        # ⬇ Noma'lum metod ham o'lchanadi: bu o'sib ketsa, Payme protokolni
        # o'zgartirgan yoki kimdir bizga soxta so'rov yuboryapti demakdir.
        metrics.payment_webhook_received.labels(method=method, result="unsupported").inc()
        return PaymeError(ERR_METHOD_NOT_FOUND, f"Metod topilmadi: {method}").as_response()

    if not isinstance(params, dict):
        return PaymeError(ERR_INVALID_REQUEST, "params obyekt bo'lishi kerak").as_response()

    try:
        response = handler(params)
    except PaymeError as exc:
        response = exc.as_response()
    except (KeyError, TypeError, ValueError, InvalidOperation):
        response = PaymeError(ERR_INVALID_REQUEST, "So'rov maydonlari noto'g'ri").as_response()
    except Exception:  # noqa: BLE001 — 500 hech qachon (B3)
        logger.exception("Payme webhook kutilmagan xato: method=%s", method)
        metrics.payment_webhook_received.labels(method=method, result="error").inc()
        return PaymeError(ERR_CANNOT_PERFORM, "Ichki xato").as_response()

    if "error" in response:
        result = "error"
        metrics.provider_errors.labels(provider="payme", code=str(response["error"]["code"])).inc()
    else:
        result = "ok"
    metrics.payment_webhook_received.labels(method=method, result=result).inc()
    return response


# ---------------------------------------------------------------------------
# Umumiy tekshiruvlar
# ---------------------------------------------------------------------------


def _parse_amount(params: dict) -> Decimal:
    return Decimal(str(params["amount"])) / 100  # Payme tiyinda yuboradi


def _load_booking_from_account(params: dict) -> Booking:
    account = params.get("account")
    if not isinstance(account, dict) or set(account) != ALLOWED_ACCOUNT_FIELDS:
        # Noma'lum maydonlar jim qabul qilinmaydi (A8)
        raise PaymeError(ERR_ACCOUNT_NOT_FOUND, "Buyurtma topilmadi", "booking_id")
    booking = Booking.objects.filter(id=_safe_uuid(account["booking_id"])).first()
    if booking is None:
        raise PaymeError(ERR_ACCOUNT_NOT_FOUND, "Buyurtma topilmadi", "booking_id")
    return booking


def _safe_uuid(value):
    from uuid import UUID

    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        raise PaymeError(ERR_ACCOUNT_NOT_FOUND, "Buyurtma topilmadi", "booking_id")


def _ensure_booking_payable(booking: Booking) -> None:
    """B3: eng muhim tekshiruv — bron to'lanadigan holatda bo'lmasa, Payme
    pulni YECHMAYDI. Muammo paydo bo'lishidan oldin to'xtaydi."""
    if booking.status != Booking.Status.PENDING_PAYMENT or not schedule_services.is_hold_active(
        booking.slot_id
    ):
        raise PaymeError(
            ERR_BOOKING_UNAVAILABLE, "Buyurtma muddati o'tgan yoki bekor qilingan", "booking_id"
        )


def _pending_payment_for(booking: Booking) -> Payment:
    payment = (
        Payment.objects.filter(
            booking=booking,
            provider=Payment.Provider.PAYME,
            status__in=[Payment.Status.CREATED, Payment.Status.PROCESSING],
        )
        .order_by("-created_at")
        .first()
    )
    if payment is None:
        raise PaymeError(ERR_ACCOUNT_NOT_FOUND, "Buyurtma uchun to'lov ochilmagan", "booking_id")
    return payment


def _validate_account_and_amount(params: dict) -> tuple[Booking, Payment]:
    booking = _load_booking_from_account(params)
    payment = _pending_payment_for(booking)
    if payment.amount != _parse_amount(params):
        raise PaymeError(ERR_INVALID_AMOUNT, "Summa mos emas")
    _ensure_booking_payable(booking)
    return booking, payment


def _get_transaction(params: dict, *, for_update: bool = False) -> Payment:
    qs = Payment.objects.filter(provider=Payment.Provider.PAYME, external_id=str(params["id"]))
    if for_update:
        qs = qs.select_for_update()
    payment = qs.first()
    if payment is None:
        raise PaymeError(ERR_TRANSACTION_NOT_FOUND, "Tranzaksiya topilmadi")
    return payment


def _state(payment: Payment) -> int:
    return {
        Payment.Status.PROCESSING: STATE_CREATED,
        Payment.Status.SUCCEEDED: STATE_PERFORMED,
        Payment.Status.CANCELLED: STATE_CANCELLED,
        Payment.Status.EXPIRED: STATE_CANCELLED,
        Payment.Status.REFUNDED: STATE_CANCELLED_AFTER_PERFORM,
    }.get(payment.status, STATE_CREATED)


def _is_timed_out(payment: Payment) -> bool:
    return (
        payment.provider_create_time is not None
        and _now_ms() - payment.provider_create_time > TRANSACTION_TIMEOUT_MS
    )


def _cancel_on_timeout(payment: Payment) -> None:
    Payment.objects.filter(id=payment.id, status=Payment.Status.PROCESSING).update(
        status=Payment.Status.CANCELLED,
        cancel_time=_now_ms(),
        cancel_reason=CANCEL_REASON_TIMEOUT,
        updated_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# Metodlar
# ---------------------------------------------------------------------------


def _check_perform(params: dict) -> dict:
    _, payment = _validate_account_and_amount(params)
    # B12: fiskal chek elementlari — Payme chekni shu ma'lumotdan chiqaradi
    return {"result": {"allow": True, "detail": fiscal.payme_detail(payment)}}


def _create(params: dict) -> dict:
    transaction_id = str(params["id"])

    existing = Payment.objects.filter(
        provider=Payment.Provider.PAYME, external_id=transaction_id
    ).first()
    if existing is not None:
        # IDEMPOTENT: shu `id` bilan qayta kelsa — o'sha holat
        if existing.status != Payment.Status.PROCESSING:
            raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiyani bajarib bo'lmaydi")
        if _is_timed_out(existing):
            # Tranzaksiyadan TASHQARIDA: xato ko'tarilganda rollback bo'lmasin
            _cancel_on_timeout(existing)
            raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiya muddati o'tgan")
        return _create_result(existing)

    with transaction.atomic():

        booking, payment = _validate_account_and_amount(params)
        payment = Payment.objects.select_for_update().get(id=payment.id)
        if payment.status == Payment.Status.PROCESSING:
            # Shu bronga BOSHQA `id` bilan tranzaksiya allaqachon ochilgan
            raise PaymeError(
                ERR_BOOKING_UNAVAILABLE, "Buyurtma bo'yicha boshqa tranzaksiya jarayonda", "booking_id"
            )

        now_ms = _now_ms()
        Payment.objects.filter(id=payment.id).update(
            status=Payment.Status.PROCESSING,
            external_id=transaction_id,
            provider_create_time=now_ms,
            provider_state={"payme_time": params.get("time")},
            updated_at=timezone.now(),
        )
        # B3: mijoz to'lov sahifasida — hold'ni uzaytiramiz
        schedule_services.extend_hold(booking.slot_id, PAYMENT_HOLD_EXTENSION_MINUTES)

    payment.refresh_from_db()
    return _create_result(payment)


def _create_result(payment: Payment) -> dict:
    return {
        "result": {
            "create_time": payment.provider_create_time,
            "transaction": str(payment.id),
            "state": STATE_CREATED,
        }
    }


def _perform_result(payment: Payment) -> dict:
    return {
        "result": {
            "transaction": str(payment.id),
            "perform_time": payment.perform_time,
            "state": STATE_PERFORMED,
        }
    }


def _perform(params: dict) -> dict:
    """Pul yechiladi -> bron tasdiqlanadi.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  IDEMPOTENT. Payme tarmoq muammosida AYNAN SHU so'rovni qayta         ║
    ║  yuboradi — ikkinchi marta birinchi natija qaytariladi.               ║
    ║                                                                       ║
    ║  FAIL-SAFE (B3): bronni tasdiqlab bo'lmasa ham (poyga holati), pul    ║
    ║  allaqachon yechilgan — haqiqatni yozamiz (`succeeded` +              ║
    ║  `needs_refund`), alert chiqaramiz va Payme'ga MUVAFFAQIYAT           ║
    ║  qaytaramiz. 500 EMAS.                                                ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    candidate = _get_transaction(params)
    if candidate.status == Payment.Status.PROCESSING and _is_timed_out(candidate):
        # Tranzaksiyadan TASHQARIDA: xato ko'tarilganda rollback bo'lmasin
        _cancel_on_timeout(candidate)
        raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiya muddati o'tgan")

    with transaction.atomic():
        payment = _get_transaction(params, for_update=True)

        if payment.status == Payment.Status.SUCCEEDED:
            return _perform_result(payment)
        if payment.status != Payment.Status.PROCESSING:
            raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiyani bajarib bo'lmaydi")

        try:
            settlement.settle_success(payment, perform_time=_now_ms())
        except settlement.AlreadyPaidElsewhere:
            # Bron boshqa to'lov bilan allaqachon to'langan — ikkinchi marta yechmaymiz
            raise PaymeError(ERR_CANNOT_PERFORM, "Buyurtma allaqachon to'langan")

    payment.refresh_from_db()
    settlement.observe_success(payment)
    return _perform_result(payment)


def _cancel_result(payment: Payment) -> dict:
    return {
        "result": {
            "transaction": str(payment.id),
            "cancel_time": payment.cancel_time,
            "state": _state(payment),
        }
    }


def _cancel(params: dict) -> dict:
    """Bekor qilish.

    state 1 -> -1: pul yechilmagan. Bron `pending_payment` da qoladi — mijoz
        hold muddati ichida qayta to'lashi mumkin; bo'lmasa bron o'z-o'zidan
        muddati o'tadi.
    state 2 -> -2: pul qaytarildi — SAGA KOMPENSATSIYASI: bron bekor, slot bo'sh.
    """
    reason = params.get("reason")

    with transaction.atomic():
        payment = _get_transaction(params, for_update=True)

        if payment.status in (
            Payment.Status.CANCELLED,
            Payment.Status.EXPIRED,
            Payment.Status.REFUNDED,
        ):
            return _cancel_result(payment)  # idempotent

        if payment.status in (Payment.Status.SUCCEEDED, Payment.Status.PARTIALLY_REFUNDED):
            # Pul Payme tomonidan qaytarildi (kabinetda bekor qilindi).
            # Navbatdagi / qo'lda kutilayotgan refund'lar shu bilan yopiladi.
            for refund in payment.refunds.exclude(status=Refund.Status.SUCCEEDED):
                refund_services.mark_refund_succeeded(refund.id, external_refund_id=str(params["id"]))
            # Bizda so'ralmagan qism (kabinetdan to'g'ridan bekor) — ham Refund
            # sifatida yoziladi, aks holda ledger va reconciliation buziladi
            remaining = refund_services.get_paid_amount(payment.booking_id)
            if remaining > 0:
                extra = refund_services.request_refund(
                    booking_id=payment.booking_id, amount=remaining,
                    reason="provider_cancelled", initiated_by="system",
                )
                if extra is not None:
                    refund_services.mark_refund_succeeded(extra.id, external_refund_id=str(params["id"]))
            Payment.objects.filter(id=payment.id).update(
                status=Payment.Status.REFUNDED,
                cancel_time=_now_ms(),
                cancel_reason=reason,
                updated_at=timezone.now(),
            )
            try:
                with transaction.atomic():
                    booking_services.cancel_booking(
                        payment.booking_id,
                        reason="To'lov qaytarildi (Payme)",
                        actor="system",
                        issue_refund=False,
                    )
            except booking_services.BookingError as exc:
                # Pul qaytdi, bronni bekor qilib bo'lmadi (masalan qabul bo'lib o'tgan).
                # Payme'ga baribir muvaffaqiyat — pul harakati haqiqat.
                logger.warning("Payme refund: bron bekor qilinmadi: %s (%s)", payment.booking_id, exc)
            compensated = True
        else:
            Payment.objects.filter(id=payment.id).update(
                status=Payment.Status.CANCELLED,
                cancel_time=_now_ms(),
                cancel_reason=reason,
                updated_at=timezone.now(),
            )
            compensated = False

    if compensated:
        # Commit'dan KEYIN sanaymiz: rollback bo'lsa kompensatsiya bo'lmagan
        metrics.saga_compensations.inc()

    payment.refresh_from_db()
    return _cancel_result(payment)


def _check(params: dict) -> dict:
    payment = _get_transaction(params)
    return {
        "result": {
            "create_time": payment.provider_create_time,
            "perform_time": payment.perform_time or 0,
            "cancel_time": payment.cancel_time or 0,
            "transaction": str(payment.id),
            "state": _state(payment),
            "reason": payment.cancel_reason,
        }
    }


def _get_statement(params: dict) -> dict:
    frm, to = int(params["from"]), int(params["to"])
    payments = Payment.objects.filter(
        provider=Payment.Provider.PAYME,
        provider_create_time__gte=frm,
        provider_create_time__lte=to,
    ).order_by("provider_create_time")
    return {
        "result": {
            "transactions": [
                {
                    "id": p.external_id,
                    "time": (p.provider_state or {}).get("payme_time") or p.provider_create_time,
                    "amount": int(p.amount * 100),
                    "account": {"booking_id": str(p.booking_id)},
                    "create_time": p.provider_create_time,
                    "perform_time": p.perform_time or 0,
                    "cancel_time": p.cancel_time or 0,
                    "transaction": str(p.id),
                    "state": _state(p),
                    "reason": p.cancel_reason,
                }
                for p in payments
            ]
        }
    }


def _set_fiscal_data(params: dict) -> dict:
    """Fiskal chek ma'lumoti (B12) — saqlaymiz, tahlil keyinroq."""
    payment = _get_transaction(params)
    state = dict(payment.provider_state or {})
    fiscal = state.setdefault("fiscal", {})
    fiscal[str(params.get("type", "PERFORM"))] = params.get("fiscal_data")
    Payment.objects.filter(id=payment.id).update(provider_state=state, updated_at=timezone.now())
    return {"result": {"success": True}}


__all__ = ["handle_payme_webhook", "PaymentError"]
