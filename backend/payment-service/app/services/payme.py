"""Payme Merchant API (JSON-RPC webhook) — to'liq metodlar to'plami (B2).

    CheckPerformTransaction  — to'lovdan OLDIN: qabul qila olamizmi? (booking `/payable` — sinxron) + fiskal `detail`
    CreateTransaction        — tranzaksiya ochildi (pul hali yechilmagan); booking hold'ni uzaytiradi
    PerformTransaction       — pul yechildi → succeeded + ledger + `payment.captured` (bron — event orqali)
    CancelTransaction        — bekor (1 → −1) yoki provayder qaytardi (2 → −2: `payment.reversed`)
    CheckTransaction, GetStatement, SetFiscalData

Holatlar: 1 yaratilgan, 2 bajarilgan, −1 yaratilgandan bekor, −2 bajarilgandan bekor.

QOIDA (B3): bu yerdan HECH QACHON istisno chiqib 500 bo'lmaydi — Payme 500 ni "javob bermadi" deb qayta
yuboradi, pul esa yechilgan bo'lishi mumkin. Har doim HTTP 200 + `error`.
Himoya (A8): IP allowlist, Basic Auth (`Paycom:<kalit>`, compare_digest, kalit bo'sh — fail-closed), tana hajmi,
har chaqiruv `provider_logs` ga.

⚠️ Kodlar/maydonlarni joriy rasmiy Payme Merchant API hujjati bilan solishtiring.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.db import transaction
from medbron_common.money import share_of

from app.core.config import get_settings
from app.metrics import PROVIDER_ERRORS, SAGA_COMPENSATIONS, WEBHOOKS
from app.models import Payment
from app.models.payment import (
    CANCELLED,
    CREATED,
    EXPIRED,
    PARTIALLY_REFUNDED,
    PAYME,
    PROCESSING,
    REFUNDED,
    REVERSED,
    SUCCEEDED,
)
from app.repositories import payments as repo
from app.services import fiscal, journal, ledger, payloads, refunds, settlement
from app.services.deps import Deps

logger = logging.getLogger(__name__)

STATE_CREATED, STATE_PERFORMED, STATE_CANCELLED, STATE_CANCELLED_AFTER_PERFORM = 1, 2, -1, -2
CANCEL_REASON_TIMEOUT = 4

ERR_INVALID_AMOUNT = -31001
ERR_TRANSACTION_NOT_FOUND = -31003
ERR_CANNOT_CANCEL = -31007
ERR_CANNOT_PERFORM = -31008
ERR_ACCOUNT_NOT_FOUND = -31050
ERR_BOOKING_UNAVAILABLE = -31099
ERR_INVALID_JSON = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_UNAUTHORIZED = -32504

ALLOWED_ACCOUNT_FIELDS = {"booking_id"}
TAKEN_STATUSES = (SUCCEEDED, PARTIALLY_REFUNDED, REFUNDED)


class PaymeError(Exception):
    def __init__(self, code: int, message: str, data: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def as_response(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": {"uz": self.message, "ru": self.message, "en": self.message},
        }
        if self.data:
            error["data"] = self.data
        return {"error": error}


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- kirish nuqtasi


def verify_auth(authorization: str | None) -> bool:
    """⚠️ BUNI TEKSHIRMASLIK — eng xavfli xato: hujumchi `PerformTransaction` ni o'zi yuborib, pul to'lamasdan
    bronni tasdiqlatadi. Kalit sozlanmagan bo'lsa — hamma so'rov rad etiladi."""
    secret = get_settings().payme_secret_key
    if not secret or not authorization or not authorization.startswith("Basic "):
        return False
    try:
        login, _, key = base64.b64decode(authorization[6:], validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return login == "Paycom" and hmac.compare_digest(key.encode(), secret.get_secret_value().encode())


async def webhook(
    session: AsyncSession, deps: Deps, *, body: bytes, authorization: str | None, ip: str | None
) -> dict[str, Any]:
    """To'liq JSON-RPC javobi. Hech qachon istisno chiqarmaydi."""
    started = time.monotonic()
    parsed: dict[str, Any] = {}
    result = await _dispatch(session, deps, body, authorization, ip, parsed)
    params = parsed.get("params")
    await journal.write(
        provider=PAYME,
        direction="in",
        operation=str(parsed.get("method", ""))[:64] or "invalid",
        request=params if isinstance(params, dict) else {},
        response=result,
        ip=ip,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    return {"jsonrpc": "2.0", "id": parsed.get("id"), **result}


async def _dispatch(
    session: AsyncSession, deps: Deps, body: bytes, authorization: str | None, ip: str | None, parsed: dict[str, Any]
) -> dict[str, Any]:
    settings = get_settings()
    if settings.payme_ips and ip not in settings.payme_ips:
        return PaymeError(ERR_UNAUTHORIZED, "Ruxsat yo'q").as_response()
    if len(body) > settings.webhook_max_body_bytes:
        return PaymeError(ERR_INVALID_REQUEST, "So'rov juda katta").as_response()
    if not verify_auth(authorization):
        return PaymeError(ERR_UNAUTHORIZED, "Ruxsat yo'q").as_response()
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return PaymeError(ERR_INVALID_JSON, "JSON xato").as_response()
    if not isinstance(data, dict) or "method" not in data or "params" not in data:
        return PaymeError(ERR_INVALID_REQUEST, "So'rov noto'g'ri").as_response()
    parsed.update(data)
    return await handle(session, deps, method=str(data["method"]), params=data["params"])


async def handle(session: AsyncSession, deps: Deps, *, method: str, params: Any) -> dict[str, Any]:
    handlers: dict[str, Callable[[AsyncSession, Deps, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
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
        # Noma'lum metod ham o'lchanadi: o'sib ketsa — Payme protokolni o'zgartirgan yoki soxta so'rovlar
        WEBHOOKS.labels(method="unsupported", result="error").inc()
        return PaymeError(ERR_METHOD_NOT_FOUND, f"Metod topilmadi: {method}").as_response()
    if not isinstance(params, dict):
        return PaymeError(ERR_INVALID_REQUEST, "params obyekt bo'lishi kerak").as_response()
    try:
        response = await handler(session, deps, params)
    except PaymeError as exc:
        response = exc.as_response()
    except (KeyError, TypeError, ValueError):
        await session.rollback()
        response = PaymeError(ERR_INVALID_REQUEST, "So'rov maydonlari noto'g'ri").as_response()
    except Exception:  # B3: 500 hech qachon
        await session.rollback()
        logger.exception("payme webhook unexpected error", extra={"method": method})
        response = PaymeError(ERR_CANNOT_PERFORM, "Ichki xato").as_response()
    if "error" in response:
        PROVIDER_ERRORS.labels(provider=PAYME, code=str(response["error"]["code"])).inc()
    WEBHOOKS.labels(method=method, result="error" if "error" in response else "ok").inc()
    return response


# --------------------------------------------------------------------------- umumiy tekshiruvlar


def _booking_id(params: dict[str, Any]) -> uuid.UUID:
    account = params.get("account")
    if not isinstance(account, dict) or set(account) != ALLOWED_ACCOUNT_FIELDS:
        # Noma'lum maydonlar jim qabul qilinmaydi (A8)
        raise PaymeError(ERR_ACCOUNT_NOT_FOUND, "Buyurtma topilmadi", "booking_id")
    try:
        return uuid.UUID(str(account["booking_id"]))
    except ValueError:
        raise PaymeError(ERR_ACCOUNT_NOT_FOUND, "Buyurtma topilmadi", "booking_id") from None


def _amount(params: dict[str, Any]) -> int:
    amount = params["amount"]
    if isinstance(amount, bool) or not isinstance(amount, int | float) or int(amount) != amount:
        raise PaymeError(ERR_INVALID_AMOUNT, "Summa noto'g'ri")
    return int(amount)  # Payme tiyinda yuboradi


async def _validate(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> Payment:
    """Hisob (bron) → ochiq Payme to'lovi → summa → bron hali to'lanadimi (B3: aks holda pul YECHILMAYDI)."""
    booking_id = _booking_id(params)
    p = await repo.open_for(session, booking_id, PAYME)
    if p is None:
        raise PaymeError(ERR_ACCOUNT_NOT_FOUND, "Buyurtma uchun to'lov ochilmagan", "booking_id")
    if p.amount_tiyin != _amount(params):
        raise PaymeError(ERR_INVALID_AMOUNT, "Summa mos emas")
    b = await deps.booking.payable(booking_id)
    if b is None or not b.payable:
        raise PaymeError(ERR_BOOKING_UNAVAILABLE, "Buyurtma muddati o'tgan yoki bekor qilingan", "booking_id")
    return p


async def _transaction(session: AsyncSession, params: dict[str, Any], *, for_update: bool = False) -> Payment:
    p = await repo.by_external(session, PAYME, str(params["id"]), for_update=for_update)
    if p is None:
        raise PaymeError(ERR_TRANSACTION_NOT_FOUND, "Tranzaksiya topilmadi")
    return p


def state_of(p: Payment) -> int:
    if p.status in TAKEN_STATUSES:
        return STATE_PERFORMED  # bizning refundlarimiz Payme tranzaksiyasi holatini o'zgartirmaydi
    if p.status in (CANCELLED, EXPIRED):
        return STATE_CANCELLED
    if p.status == REVERSED:
        return STATE_CANCELLED_AFTER_PERFORM
    return STATE_CREATED


def _timed_out(p: Payment) -> bool:
    timeout_ms = get_settings().payme_transaction_timeout_hours * 3600 * 1000
    return p.provider_create_time is not None and now_ms() - p.provider_create_time > timeout_ms


async def _expire(session: AsyncSession, p: Payment) -> None:
    """12 soatlik muddat (−1, reason 4). O'z tranzaksiyasida — keyin ko'tariladigan xato uni rollback qilmasin."""
    async with transaction(session):
        locked = await repo.get(session, p.id, for_update=True)
        if locked is not None and locked.status == PROCESSING:
            locked.status = CANCELLED
            locked.cancel_time = now_ms()
            locked.cancel_reason = CANCEL_REASON_TIMEOUT
            payloads.failed(session, locked, reason="timeout")


# --------------------------------------------------------------------------- metodlar


async def _check_perform(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> dict[str, Any]:
    p = await _validate(session, deps, params)
    # B12: Payme chekni shu `detail` dan chiqaradi (provayder OFD'si)
    return {"result": {"allow": True, "detail": fiscal.payme_detail(p.receipt_items)}}


def _create_result(p: Payment) -> dict[str, Any]:
    return {"result": {"create_time": p.provider_create_time, "transaction": str(p.id), "state": STATE_CREATED}}


async def _create(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> dict[str, Any]:
    external_id = str(params["id"])
    existing = await repo.by_external(session, PAYME, external_id)
    if existing is not None:
        # IDEMPOTENT: shu `id` bilan qayta kelsa — o'sha natija
        if existing.status != PROCESSING:
            raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiyani bajarib bo'lmaydi")
        if _timed_out(existing):
            await _expire(session, existing)
            raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiya muddati o'tgan")
        return _create_result(existing)

    candidate = await _validate(session, deps, params)
    try:
        async with transaction(session):
            p = await repo.get(session, candidate.id, for_update=True)
            if p is None or p.status == PROCESSING:
                # Shu bronga BOSHQA `id` bilan tranzaksiya allaqachon ochilgan
                raise PaymeError(
                    ERR_BOOKING_UNAVAILABLE, "Buyurtma bo'yicha boshqa tranzaksiya jarayonda", "booking_id"
                )
            if p.status != CREATED:
                raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiyani bajarib bo'lmaydi")
            p.status = PROCESSING
            p.external_id = external_id
            p.provider_create_time = now_ms()
            p.provider_state = {**p.provider_state, "payme_time": params.get("time")}
    except IntegrityError:
        raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiya allaqachon mavjud") from None
    await _extend_hold(deps, p.booking_id)
    return _create_result(p)


async def _extend_hold(deps: Deps, booking_id: uuid.UUID) -> None:
    """B3: mijoz to'lov sahifasida — hold uzaytiriladi. Best effort: xato to'lovni to'xtatmaydi (Perform baribir
    `payment.captured` beradi, booking bron holatiga qarab tasdiqlaydi yoki kompensatsiya qiladi)."""
    try:
        await deps.booking.extend_payment(booking_id)
    except Exception:
        logger.warning("extend-payment failed", extra={"booking_id": str(booking_id)}, exc_info=True)


def _perform_result(p: Payment) -> dict[str, Any]:
    return {"result": {"transaction": str(p.id), "perform_time": p.perform_time, "state": STATE_PERFORMED}}


async def _perform(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> dict[str, Any]:
    """╔═══════════════════════════════════════════════════════════════════════════════╗
    ║ IDEMPOTENT: Payme tarmoq muammosida AYNAN shu so'rovni qayta yuboradi —           ║
    ║ ikkinchi marta birinchi natija qaytariladi. Bron tasdig'i — event orqali (saga).  ║
    ╚═══════════════════════════════════════════════════════════════════════════════╝"""
    candidate = await _transaction(session, params)
    if candidate.status == PROCESSING and _timed_out(candidate):
        await _expire(session, candidate)
        raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiya muddati o'tgan")
    try:
        async with transaction(session):
            p = await _transaction(session, params, for_update=True)
            if p.status in TAKEN_STATUSES:
                return _perform_result(p)
            if p.status != PROCESSING:
                raise PaymeError(ERR_CANNOT_PERFORM, "Tranzaksiyani bajarib bo'lmaydi")
            await settlement.settle_success(session, p, perform_time=now_ms())
    except (settlement.AlreadyPaidElsewhere, IntegrityError):
        raise PaymeError(ERR_CANNOT_PERFORM, "Buyurtma allaqachon to'langan") from None
    settlement.observe_success(p)
    return _perform_result(p)


def _cancel_result(p: Payment) -> dict[str, Any]:
    return {"result": {"transaction": str(p.id), "cancel_time": p.cancel_time, "state": state_of(p)}}


async def _cancel(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> dict[str, Any]:
    """1 → −1: pul yechilmagan; bron `pending_payment` da qoladi (muddat ichida qayta to'lash mumkin).
    2 → −2: pul Payme kabinetida qaytarildi — SAGA KOMPENSATSIYASI: ochiq refundlar yopiladi, qolgan summa
    `payment.reversed` bilan (booking bronni bekor qiladi, billing shifokor ulushini ushlaydi)."""
    reason = params.get("reason")
    reason = int(reason) if isinstance(reason, int) and not isinstance(reason, bool) else None
    compensated = False
    async with transaction(session):
        p = await _transaction(session, params, for_update=True)
        if p.status in (CANCELLED, EXPIRED, REVERSED):
            return _cancel_result(p)  # idempotent
        if p.status in TAKEN_STATUSES:
            await reverse(session, p, external_ref=str(params["id"]), provider_reason=reason)
            compensated = True
        else:
            p.status = CANCELLED
            p.cancel_reason = reason
            payloads.failed(session, p, reason=f"payme_cancelled:{reason}")
        p.cancel_time = now_ms()
    if compensated:
        SAGA_COMPENSATIONS.labels(kind="payment_reversed").inc()
    return _cancel_result(p)


async def reverse(session: AsyncSession, p: Payment, *, external_ref: str, provider_reason: int | None) -> None:
    """Provayder yechilgan pulni o'zi qaytardi. `p` — FOR UPDATE bilan, tranzaksiya ichida."""
    for r in await repo.refunds_of(session, p.id):
        if r.status != "succeeded":
            locked = await repo.refund(session, r.id, for_update=True)
            assert locked is not None
            await refunds.mark_succeeded(session, locked, external_refund_id=external_ref)
    refunded = await repo.refunded_total(session, p.id, only_succeeded=True)
    remaining = p.amount_tiyin - refunded
    if remaining > 0:
        captured = share_of(p.money, p.amount_tiyin).doctor_tiyin
        returned = sum(r.doctor_share_tiyin for r in await repo.refunds_of(session, p.id) if r.status == "succeeded")
        doctor_share = max(0, captured - returned)
        await ledger.post_reversal(session, p, amount_tiyin=remaining, doctor_share_tiyin=doctor_share)
        payloads.reversed_(
            session, p, amount_tiyin=remaining, doctor_share_tiyin=doctor_share, provider_reason=provider_reason
        )
    p.status = REVERSED
    p.cancel_reason = provider_reason


async def _check(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> dict[str, Any]:
    p = await _transaction(session, params)
    return {
        "result": {
            "create_time": p.provider_create_time,
            "perform_time": p.perform_time or 0,
            "cancel_time": p.cancel_time or 0,
            "transaction": str(p.id),
            "state": state_of(p),
            "reason": p.cancel_reason,
        }
    }


async def _get_statement(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> dict[str, Any]:
    payments = await repo.in_window(session, PAYME, int(params["from"]), int(params["to"]))
    return {
        "result": {
            "transactions": [
                {
                    "id": p.external_id,
                    "time": p.provider_state.get("payme_time") or p.provider_create_time,
                    "amount": p.amount_tiyin,
                    "account": {"booking_id": str(p.booking_id)},
                    "create_time": p.provider_create_time,
                    "perform_time": p.perform_time or 0,
                    "cancel_time": p.cancel_time or 0,
                    "transaction": str(p.id),
                    "state": state_of(p),
                    "reason": p.cancel_reason,
                }
                for p in payments
            ]
        }
    }


async def _set_fiscal_data(session: AsyncSession, deps: Deps, params: dict[str, Any]) -> dict[str, Any]:
    """Payme chiqargan fiskal chek ma'lumoti (QR, raqam) — saqlanadi."""
    async with transaction(session):
        p = await _transaction(session, params, for_update=True)
        fiscal_state = dict(p.provider_state.get("fiscal") or {})
        fiscal_state[str(params.get("type", "PERFORM"))] = params.get("fiscal_data")
        p.provider_state = {**p.provider_state, "fiscal": fiscal_state}
        if p.fiscal_submitted_at is None:
            p.fiscal_submitted_at = datetime.now(UTC)
    return {"result": {"success": True}}
