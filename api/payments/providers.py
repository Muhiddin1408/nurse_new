"""
payments/providers.py — to'lov provayderi abstraksiyasi (B5).

Platforma provayderga O'ZI murojaat qiladigan yagona joy: refund, holat so'rash,
reconciliation (GetStatement). Click qo'shish = `ClickProvider` klassi yozish.

Har bir chaqiruv `call()` orqali o'tadi:
    * timeout (connect 3 s, read 10 s)
    * retry — FAQAT idempotent operatsiyalarda, exponential backoff + jitter
    * circuit breaker — 5 ta ketma-ket xato -> 60 s ochiq
    * har bir so'rov/javob `ProviderRequestLog` ga
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Protocol

from api.notifications.circuit import CircuitBreaker
from api.observability import metrics
from apps.payment.models import Payment, ProviderRequestLog

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 10
MAX_RETRIES = 3


class ProviderError(Exception):
    """Vaqtinchalik xato — qayta urinish mumkin."""


class ProviderUnavailable(ProviderError):
    """Circuit breaker ochiq."""


class ManualRefundRequired(Exception):
    """Provayder API orqali avtomat qaytarib bo'lmaydi — qo'lda (kabinetda) qilinadi."""


@dataclass(frozen=True)
class RefundDTO:
    external_refund_id: str
    status: str  # "succeeded" | "pending"


@dataclass(frozen=True)
class TransactionDTO:
    external_id: str
    amount: Decimal
    state: int
    booking_id: str
    create_time: int
    perform_time: int
    cancel_time: int


class PaymentProvider(Protocol):
    name: str

    def refund(self, payment: Payment, amount: Decimal, reason: str) -> RefundDTO: ...

    def get_statement(self, frm: datetime, to: datetime) -> list[TransactionDTO]: ...


_breakers: dict[str, CircuitBreaker] = {}


def _breaker(provider: str) -> CircuitBreaker:
    return _breakers.setdefault(provider, CircuitBreaker(failure_threshold=5, recovery_timeout=60.0))


def call(provider: str, operation: str, request: dict, fn: Callable[[], dict], *, idempotent: bool):
    breaker = _breaker(provider)
    attempts = MAX_RETRIES if idempotent else 1
    last_exc: Exception | None = None

    for attempt in range(attempts):
        if not breaker.allow():
            raise ProviderUnavailable(f"{provider}: circuit breaker ochiq")
        started = time.monotonic()
        try:
            response = fn()
        except ProviderError as exc:
            breaker.record_failure()
            metrics.provider_errors.labels(provider=provider, code=type(exc).__name__).inc()
            _log(provider, operation, request, {}, str(exc), started)
            last_exc = exc
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt, 8) * 0.1 + random.uniform(0, 0.1))
            continue
        breaker.record_success()
        _log(provider, operation, request, response, "", started)
        return response

    raise last_exc  # type: ignore[misc]


def _log(provider, operation, request, response, error, started) -> None:
    ProviderRequestLog.objects.create(
        provider=provider,
        operation=operation,
        request=request,
        response=response if isinstance(response, dict) else {"raw": str(response)},
        error=error[:2000],
        duration_ms=int((time.monotonic() - started) * 1000),
    )


class PaymeProvider:
    """Payme Merchant API modeli.

    ⚠️ Merchant API'da savdogar refund'ni O'ZI boshlay olmaydi: bajarilgan
    tranzaksiyani Payme kabinetida bekor qilish kerak, shunda Payme bizga
    `CancelTransaction` (state -2) yuboradi va refund `succeeded` bo'ladi.
    Qisman qaytarish ham Merchant API'da yo'q.

    Shuning uchun `refund` `ManualRefundRequired` ko'taradi -> Refund
    `manual_required` navbatiga tushadi va alert chiqadi. Payme bilan
    avtomat refund kelishilsa (Business/Subscribe API), shu metod yoziladi.
    """

    name = Payment.Provider.PAYME

    def refund(self, payment: Payment, amount: Decimal, reason: str) -> RefundDTO:
        raise ManualRefundRequired(
            "Payme Merchant API: refund Payme kabinetida tranzaksiyani bekor qilish orqali"
        )

    def get_statement(self, frm: datetime, to: datetime) -> list[TransactionDTO]:
        # Payme GetStatement'ni O'ZI chaqiradi (inbound). Reconciliation uchun
        # Payme bizga yuborgan tranzaksiyalar bazada — B7 da reestr fayli
        # yoki Business API bilan solishtiriladi.
        raise ManualRefundRequired("Payme statement API ulanmagan")


class ClickProvider:
    """Click Merchant API (B6). Refund — `payment/reversal`: FAQAT to'liq summa.

    Qisman qaytarish Click Merchant API'da yo'q -> `ManualRefundRequired`
    (kabinetda qo'lda), Payme bilan bir xil navbat va alert.
    Auth sarlavhasi: `<merchant_user_id>:<sha1(timestamp + secret_key)>:<timestamp>`.
    """

    name = Payment.Provider.CLICK

    def _headers(self) -> dict:
        import hashlib
        from django.conf import settings

        ts = str(int(time.time()))
        digest = hashlib.sha1((ts + settings.CLICK_SECRET_KEY).encode()).hexdigest()  # nosec B324 — Click protokoli
        return {"Auth": f"{settings.CLICK_MERCHANT_USER_ID}:{digest}:{ts}", "Accept": "application/json"}

    def refund(self, payment: Payment, amount: Decimal, reason: str) -> RefundDTO:
        from django.conf import settings

        paydoc_id = (payment.provider_state or {}).get("click_paydoc_id")
        if not (settings.CLICK_MERCHANT_USER_ID and settings.CLICK_SECRET_KEY and paydoc_id):
            raise ManualRefundRequired("Click Merchant API sozlanmagan yoki paydoc_id yo'q")
        if Decimal(amount) != payment.amount:
            raise ManualRefundRequired("Click: qisman qaytarish API'da yo'q — kabinetda")

        url = f"{settings.CLICK_API_URL}/payment/reversal/{settings.CLICK_SERVICE_ID}/{paydoc_id}"

        def do() -> dict:
            import requests

            try:
                r = requests.delete(url, headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            except requests.RequestException as exc:
                raise ProviderError(f"Click tarmoq xatosi: {exc}") from exc
            if r.status_code >= 500:
                raise ProviderError(f"Click {r.status_code}")
            return r.json()

        # Reversal idempotent: ikkinchi so'rov "allaqachon qaytarilgan" xatosini beradi
        data = call("click", "reversal", {"paydoc_id": paydoc_id, "amount": str(amount)}, do, idempotent=True)
        code = int(data.get("error_code", -1))
        if code == 0:
            return RefundDTO(external_refund_id=str(data.get("payment_id", paydoc_id)), status="succeeded")
        raise ManualRefundRequired(f"Click reversal rad etdi: {code} {data.get('error_note', '')}")

    def submit_fiscal(self, payment: Payment) -> dict:
        """B12: to'lov uchun fiskal chek elementlari (`payment/ofd_data/submit_items`)."""
        from django.conf import settings

        from api.payments import fiscal

        paydoc_id = (payment.provider_state or {}).get("click_paydoc_id")
        if not (settings.CLICK_MERCHANT_USER_ID and settings.CLICK_SECRET_KEY and paydoc_id):
            raise ManualRefundRequired("Click Merchant API sozlanmagan yoki paydoc_id yo'q")
        body = {
            "service_id": int(settings.CLICK_SERVICE_ID),
            "payment_id": int(paydoc_id),
            "items": fiscal.click_ofd_items(payment),
            "received_ecash": int(payment.amount * 100),
            "received_cash": 0,
            "received_card": 0,
        }

        def do() -> dict:
            import requests

            try:
                r = requests.post(f"{settings.CLICK_API_URL}/payment/ofd_data/submit_items", json=body,
                                  headers=self._headers(), timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            except requests.RequestException as exc:
                raise ProviderError(f"Click tarmoq xatosi: {exc}") from exc
            if r.status_code >= 500:
                raise ProviderError(f"Click {r.status_code}")
            return r.json()

        return call("click", "ofd_submit_items", body, do, idempotent=True)

    def get_statement(self, frm: datetime, to: datetime) -> list[TransactionDTO]:
        # Click to'lovlari bizga webhook orqali keladi; reestr fayli bilan
        # solishtirish Payme bilan bir xil (`reconcile_payments --file`)
        raise ManualRefundRequired("Click statement API ulanmagan — reestr fayli bilan")


_PROVIDERS: dict[str, PaymentProvider] = {
    Payment.Provider.PAYME: PaymeProvider(),
    Payment.Provider.CLICK: ClickProvider(),
}


def get_provider(name: str) -> PaymentProvider:
    return _PROVIDERS[name]


def register_provider(name: str, provider: PaymentProvider) -> None:
    """Testlar va yangi provayderlar uchun."""
    _PROVIDERS[name] = provider
