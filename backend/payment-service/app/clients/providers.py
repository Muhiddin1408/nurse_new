"""To'lov provayderlariga CHIQUVCHI so'rovlar (refund, fiskal chek) — platforma provayderga o'zi murojaat
qiladigan yagona joy. Kiruvchi webhooklar — services/payme.py, services/click.py.

Har chaqiruv `ProviderGateway.call()` orqali:
    * timeout (connect 3 s, read 10 s)
    * retry — FAQAT idempotent operatsiyalarda (3 marta, backoff + jitter)
    * circuit breaker — 5 ta ketma-ket xato → 60 s ochiq
    * har so'rov/javob `journal` ga (services qatlami `provider_logs` ga yozadi; imzo/kalit yozilmaydi)

Ichki servislar uchun `ServiceClient` bu yerda ishlatilmaydi: u `X-Internal-Token` qo'shadi — tashqi
provayderga ichki token HECH QACHON yuborilmaydi.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from medbron_common.circuit import CircuitBreaker

from app.core.config import Settings, get_settings
from app.metrics import PROVIDER_ERRORS

MAX_RETRIES = 3


class ProviderError(Exception):
    """Vaqtinchalik xato — qayta urinish mumkin."""


class ProviderUnavailable(ProviderError):
    """Circuit breaker ochiq."""


class ManualRefundRequired(Exception):
    """Provayder API orqali avtomat bajarib bo'lmaydi — kabinetda qo'lda (Payme refund, Click qisman refund)."""


@dataclass(frozen=True)
class Exchange:
    provider: str
    operation: str
    request: dict[str, Any]
    response: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: int = 0


Journal = Callable[[Exchange], Awaitable[None]]


async def _no_journal(_: Exchange) -> None:
    return None


@dataclass(frozen=True)
class RefundResult:
    external_refund_id: str


class ProviderGateway:
    def __init__(self, name: str, *, journal: Journal = _no_journal, transport: httpx.AsyncBaseTransport | None = None):
        settings = get_settings()
        self.name = name
        self.journal = journal
        self.breaker = CircuitBreaker(f"provider:{name}", failure_threshold=5, recovery_timeout=60.0)
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.provider_read_timeout, connect=settings.provider_connect_timeout),
            transport=transport,
        )

    async def call(
        self,
        operation: str,
        request: dict[str, Any],
        send: Callable[[], Awaitable[httpx.Response]],
        *,
        idempotent: bool,
    ) -> dict[str, Any]:
        attempts = MAX_RETRIES if idempotent else 1
        last: ProviderError | None = None
        for attempt in range(attempts):
            if not self.breaker.allow():
                raise ProviderUnavailable(f"{self.name}: circuit breaker ochiq")
            started = time.monotonic()
            try:
                response = await send()
                if response.status_code >= 500:
                    raise ProviderError(f"{self.name} {response.status_code}")
                data = response.json()
                if not isinstance(data, dict):
                    raise ProviderError(f"{self.name}: javob obyekt emas")
            except (httpx.HTTPError, ValueError, ProviderError) as exc:
                self.breaker.record_failure()
                PROVIDER_ERRORS.labels(provider=self.name, code=type(exc).__name__).inc()
                error = exc if isinstance(exc, ProviderError) else ProviderError(f"{self.name} tarmoq xatosi: {exc}")
                await self.journal(Exchange(self.name, operation, request, {}, str(error)[:2000], _ms(started)))
                last = error
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2**attempt, 8) * 0.1 + random.uniform(0, 0.1))  # noqa: S311
                continue
            self.breaker.record_success()
            await self.journal(Exchange(self.name, operation, request, data, "", _ms(started)))
            return data
        assert last is not None
        raise last

    async def aclose(self) -> None:
        await self.http.aclose()


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class PaymeProvider:
    """Payme Merchant API: savdogar refund'ni O'ZI boshlay olmaydi — bajarilgan tranzaksiya Payme kabinetida
    bekor qilinadi, Payme bizga `CancelTransaction` (state −2) yuboradi va ochiq refundlar yopiladi.
    Qisman qaytarish ham yo'q. Shuning uchun refund → `manual_required` (admin navbati + alert)."""

    name = "payme"

    def __init__(self, gateway: ProviderGateway) -> None:
        self.gateway = gateway

    async def refund(self, *, paydoc_id: str | None, amount_tiyin: int, payment_amount_tiyin: int) -> RefundResult:
        raise ManualRefundRequired("Payme Merchant API: refund kabinetda tranzaksiyani bekor qilish orqali")


class ClickProvider:
    """Click Merchant API: `payment/reversal` — FAQAT to'liq summa; fiskal chek — `payment/ofd_data/submit_items`.
    Auth: `<merchant_user_id>:<sha1(timestamp + secret_key)>:<timestamp>`."""

    name = "click"

    def __init__(self, gateway: ProviderGateway, settings: Settings | None = None) -> None:
        self.gateway = gateway
        self.settings = settings or get_settings()

    def _configured(self) -> bool:
        s = self.settings
        return bool(s.click_merchant_user_id and s.click_secret_key and s.click_service_id)

    def _headers(self) -> dict[str, str]:
        assert self.settings.click_secret_key is not None
        ts = str(int(time.time()))
        secret = self.settings.click_secret_key.get_secret_value()
        digest = hashlib.sha1((ts + secret).encode()).hexdigest()  # noqa: S324 — Click protokoli SHA1 talab qiladi
        return {"Auth": f"{self.settings.click_merchant_user_id}:{digest}:{ts}", "Accept": "application/json"}

    async def refund(self, *, paydoc_id: str | None, amount_tiyin: int, payment_amount_tiyin: int) -> RefundResult:
        if not (self._configured() and paydoc_id):
            raise ManualRefundRequired("Click Merchant API sozlanmagan yoki paydoc_id yo'q")
        if amount_tiyin != payment_amount_tiyin:
            raise ManualRefundRequired("Click: qisman qaytarish API'da yo'q — kabinetda")
        url = f"{self.settings.click_api_url}/payment/reversal/{self.settings.click_service_id}/{paydoc_id}"
        # Reversal idempotent: takroriy so'rov "allaqachon qaytarilgan" xatosini beradi
        data = await self.gateway.call(
            "reversal",
            {"paydoc_id": paydoc_id, "amount_tiyin": amount_tiyin},
            lambda: self.gateway.http.delete(url, headers=self._headers()),
            idempotent=True,
        )
        code = int(data.get("error_code", -1))
        if code == 0:
            return RefundResult(external_refund_id=str(data.get("payment_id", paydoc_id)))
        raise ManualRefundRequired(f"Click reversal rad etdi: {code} {data.get('error_note', '')}")

    async def submit_fiscal(self, *, paydoc_id: str | None, amount_tiyin: int, items: list[dict[str, Any]]) -> None:
        if not (self._configured() and paydoc_id):
            raise ManualRefundRequired("Click Merchant API sozlanmagan yoki paydoc_id yo'q")
        body = {
            "service_id": int(self.settings.click_service_id),
            "payment_id": int(paydoc_id),
            "items": items,
            "received_ecash": amount_tiyin,
            "received_cash": 0,
            "received_card": 0,
        }
        url = f"{self.settings.click_api_url}/payment/ofd_data/submit_items"
        data = await self.gateway.call(
            "ofd_submit_items",
            body,
            lambda: self.gateway.http.post(url, json=body, headers=self._headers()),
            idempotent=True,
        )
        code = int(data.get("error_code", -1))
        if code != 0:
            raise ProviderError(f"Click fiskal chekni rad etdi: {code} {data.get('error_note', '')}")
