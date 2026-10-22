"""Click SHOP API (B6).

    POST /v1/payments/click/prepare    action=0: qabul qila olamizmi? (pul hali yechilmagan)
    POST /v1/payments/click/complete   action=1: pul yechildi (error=0) yoki Click bekor qildi (error<0)

Imzo (MD5, `sign_string`):
    prepare:  md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id + amount + action + sign_time)
    complete: md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id + merchant_prepare_id
                  + amount + action + sign_time)

`merchant_trans_id` — `booking_id` (checkout havolasidagi `transaction_param`); `merchant_prepare_id` — butun son,
`Payment.provider_create_time` (ms). Summa — so'mda ("1500.00"), bizda tiyin.
Payme kabi QOIDA: istisno chiqib 500 bo'lmaydi — har doim JSON + `error`. "Pul yechildi" — `settlement` (bitta).

⚠️ Kodlar/maydonlarni joriy rasmiy Click SHOP API hujjati bilan solishtiring.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.db import transaction

from app.core.config import get_settings
from app.metrics import PROVIDER_ERRORS, WEBHOOKS
from app.models.payment import CANCELLED, CLICK, CREATED, MONEY_TAKEN_STATUSES, PROCESSING
from app.repositories import payments as repo
from app.services import journal, payloads, settlement
from app.services.deps import Deps

logger = logging.getLogger(__name__)

ACTION_PREPARE, ACTION_COMPLETE = 0, 1

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


class ClickError(Exception):
    def __init__(self, code: int, note: str) -> None:
        super().__init__(note)
        self.code = code
        self.note = note


def now_ms() -> int:
    return int(time.time() * 1000)


def expected_sign(p: dict[str, Any], *, with_prepare_id: bool, secret: str) -> str:
    parts = [str(p["click_trans_id"]), str(p["service_id"]), secret, str(p["merchant_trans_id"])]
    if with_prepare_id:
        parts.append(str(p.get("merchant_prepare_id", "")))
    parts += [str(p["amount"]), str(p["action"]), str(p["sign_time"])]
    return hashlib.md5("".join(parts).encode()).hexdigest()  # noqa: S324 — Click protokoli MD5 talab qiladi


def _verify(p: dict[str, Any], *, with_prepare_id: bool) -> None:
    if any(k not in p for k in REQUIRED) or (with_prepare_id and "merchant_prepare_id" not in p):
        raise ClickError(ERR_BAD_REQUEST, "So'rov to'liq emas")
    settings = get_settings()
    if not settings.click_secret_key:  # fail-closed (Payme bilan bir xil qoida)
        raise ClickError(ERR_SIGN, "Imzo tekshiruvi sozlanmagan")
    expected = expected_sign(p, with_prepare_id=with_prepare_id, secret=settings.click_secret_key.get_secret_value())
    if not hmac.compare_digest(expected, str(p["sign_string"])):
        raise ClickError(ERR_SIGN, "Imzo noto'g'ri")
    if str(p["service_id"]) != settings.click_service_id:
        raise ClickError(ERR_BAD_REQUEST, "service_id noto'g'ri")


def _amount_tiyin(p: dict[str, Any]) -> int:
    try:
        value = Decimal(str(p["amount"]))
    except InvalidOperation:
        raise ClickError(ERR_AMOUNT, "Summa noto'g'ri") from None
    tiyin = value * 100
    if not value.is_finite() or tiyin != tiyin.to_integral_value():
        raise ClickError(ERR_AMOUNT, "Summa noto'g'ri")
    return int(tiyin)


def _booking_id(p: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(p["merchant_trans_id"]))
    except ValueError:
        raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma topilmadi") from None


async def webhook(
    session: AsyncSession, deps: Deps, *, action: int, params: dict[str, Any], ip: str | None, size: int
) -> dict[str, Any]:
    """Click kutgan to'liq JSON. Hech qachon istisno chiqarmaydi; har chaqiruv `provider_logs` ga (imzosiz)."""
    started = time.monotonic()
    settings = get_settings()
    if settings.click_ips and ip not in settings.click_ips:
        result: dict[str, Any] = {"error": ERR_SIGN, "error_note": "Ruxsat yo'q"}
    elif size > settings.webhook_max_body_bytes:
        result = {"error": ERR_BAD_REQUEST, "error_note": "So'rov juda katta"}
    else:
        result = await handle(session, deps, action, params)
    await journal.write(
        provider=CLICK,
        direction="in",
        operation="webhook_prepare" if action == ACTION_PREPARE else "webhook_complete",
        request=params,
        response=result,
        ip=ip,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return result


async def handle(session: AsyncSession, deps: Deps, action: int, params: dict[str, Any]) -> dict[str, Any]:
    base = {"click_trans_id": params.get("click_trans_id"), "merchant_trans_id": params.get("merchant_trans_id")}
    method = "click_prepare" if action == ACTION_PREPARE else "click_complete"
    try:
        if str(params.get("action")) != str(action):
            raise ClickError(ERR_ACTION, "action noto'g'ri")
        result = await (_prepare(session, deps, params) if action == ACTION_PREPARE else _complete(session, params))
        WEBHOOKS.labels(method=method, result="ok").inc()
        return {**base, **result, "error": OK, "error_note": "Success"}
    except ClickError as exc:
        PROVIDER_ERRORS.labels(provider=CLICK, code=str(exc.code)).inc()
        WEBHOOKS.labels(method=method, result="error").inc()
        return {**base, "error": exc.code, "error_note": exc.note}
    except Exception:  # B3: 500 emas — Click qayta yuboradi, pul holati noaniq qoladi
        await session.rollback()
        logger.exception("click webhook unexpected error", extra={"action": action})
        WEBHOOKS.labels(method=method, result="exception").inc()
        return {**base, "error": ERR_UPDATE_FAILED, "error_note": "Ichki xato"}


async def _prepare(session: AsyncSession, deps: Deps, p: dict[str, Any]) -> dict[str, Any]:
    _verify(p, with_prepare_id=False)
    booking_id = _booking_id(p)
    amount = _amount_tiyin(p)
    trans_id = str(p["click_trans_id"])

    existing = await repo.by_external(session, CLICK, trans_id)
    if existing is not None:  # Click Prepare'ni qayta yubordi — idempotent
        if existing.status == CANCELLED:
            raise ClickError(ERR_CANCELLED, "Tranzaksiya bekor qilingan")
        return {"merchant_prepare_id": existing.provider_create_time}
    if await repo.money_taken_for(session, booking_id) is not None:
        raise ClickError(ERR_ALREADY_PAID, "Buyurtma allaqachon to'langan")
    # B3: to'lab bo'lmaydigan bron uchun pul YECHILMAYDI
    b = await deps.booking.payable(booking_id)
    if b is None:
        raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma topilmadi")
    if not b.payable:
        raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma muddati o'tgan yoki bekor qilingan")

    try:
        async with transaction(session):
            payment = await repo.open_for(session, booking_id, CLICK, statuses=(CREATED,), for_update=True)
            if payment is None:
                raise ClickError(ERR_ORDER_NOT_FOUND, "Buyurtma uchun to'lov ochilmagan")
            if payment.amount_tiyin != amount:
                raise ClickError(ERR_AMOUNT, "Summa mos emas")
            prepare_id = now_ms()
            payment.status = PROCESSING
            payment.external_id = trans_id
            payment.provider_create_time = prepare_id
            payment.provider_state = {**payment.provider_state, "click_paydoc_id": p.get("click_paydoc_id")}
    except IntegrityError:
        raise ClickError(ERR_UPDATE_FAILED, "Tranzaksiya allaqachon mavjud") from None
    try:  # mijoz Click ilovasida to'layapti — hold uzaytiriladi (best effort, Payme bilan bir xil)
        await deps.booking.extend_payment(booking_id)
    except Exception:
        logger.warning("extend-payment failed", extra={"booking_id": str(booking_id)}, exc_info=True)
    return {"merchant_prepare_id": prepare_id}


async def _complete(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    _verify(p, with_prepare_id=True)
    amount = _amount_tiyin(p)
    cancelled_by_click = False
    try:
        async with transaction(session):
            payment = await repo.by_external(session, CLICK, str(p["click_trans_id"]), for_update=True)
            if payment is None or str(payment.provider_create_time) != str(p["merchant_prepare_id"]):
                raise ClickError(ERR_TRANSACTION_NOT_FOUND, "Tranzaksiya topilmadi")
            if payment.amount_tiyin != amount:
                raise ClickError(ERR_AMOUNT, "Summa mos emas")
            if payment.status in MONEY_TAKEN_STATUSES:  # takroriy Complete — idempotent
                return {"merchant_confirm_id": payment.perform_time}
            if payment.status != PROCESSING:
                raise ClickError(ERR_CANCELLED, "Tranzaksiya bekor qilingan")
            if int(p.get("error", 0)) < 0:
                # Click to'lovni o'zi bekor qildi (mablag' yetmadi va h.k.) — pul yechilmagan. Xato atomic'dan
                # TASHQARIDA ko'tariladi, aks holda bekor qilish rollback bo'lardi.
                payment.status = CANCELLED
                payment.cancel_time = now_ms()
                payment.provider_state = {
                    **payment.provider_state,
                    "click_error": p.get("error"),
                    "click_error_note": str(p.get("error_note", ""))[:255],
                }
                payloads.failed(session, payment, reason=f"click_error:{p.get('error')}")
                cancelled_by_click = True
            else:
                await settlement.settle_success(session, payment, perform_time=now_ms())
    except (settlement.AlreadyPaidElsewhere, IntegrityError):
        raise ClickError(ERR_ALREADY_PAID, "Buyurtma allaqachon to'langan") from None
    if cancelled_by_click:
        raise ClickError(ERR_CANCELLED, "Tranzaksiya bekor qilingan")
    settlement.observe_success(payment)
    return {"merchant_confirm_id": payment.perform_time}
