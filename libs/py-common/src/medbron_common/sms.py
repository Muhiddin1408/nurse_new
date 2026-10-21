"""SMS provayder adapterlari (ADR-0004): auth-service (OTP) va notification-service ishlatadi.

    sms = create_sms_client(SmsConfig.from_settings(settings), token_store=RedisTokenStore(redis))
    message_id = await sms.send("+998901234567", "MedBron tasdiqlash kodi: 123456")

Provayderlar:
    console — faqat logga yozadi (lokal/test). Prod'da TAQIQLANGAN (`create_sms_client` rad etadi).
    eskiz   — https://notify.eskiz.uz. Token login'dan olinadi (~30 kun), `TokenStore` da saqlanadi
              (Redis — barcha nusxalar bitta tokenni ishlatadi); 401 kelsa bir marta qayta login.

Xato: `SmsSendError`. Qaror chaqiruvchida: OTP uchun fail-closed (503), bildirishnoma uchun
keyingi kanal / qayta urinish. Circuit breaker ochiq bo'lsa so'rov provayderga umuman ketmaydi.

⚠️ Eskiz'da SMS matni (shablon) moderatsiyadan o'tgan bo'lishi SHART, aks holda rad etiladi.
⚠️ Matn (OTP kodi!) hech qachon logga yozilmaydi — faqat console provayderida.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import SecretStr

from medbron_common.circuit import CircuitBreaker
from medbron_common.phone import mask_phone

logger = logging.getLogger(__name__)

Provider = Literal["console", "eskiz"]
ESKIZ_TOKEN_TTL_SECONDS = 29 * 24 * 60 * 60
ESKIZ_TOKEN_KEY = "sms:eskiz:token"  # noqa: S105 — kesh kaliti nomi, sir emas


class SmsSendError(Exception):
    """Provayder SMS'ni qabul qilmadi yoki javob bermadi."""


class SmsClient(Protocol):
    async def send(self, phone: str, text: str) -> str:
        """Yuboradi va provayderdagi xabar ID sini qaytaradi."""
        ...

    async def aclose(self) -> None: ...


class TokenStore(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class MemoryTokenStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._values.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._values[key] = value


class RedisTokenStore:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(key, value, ex=ttl_seconds)


@dataclass(frozen=True)
class SmsConfig:
    provider: Provider = "console"
    eskiz_base_url: str = "https://notify.eskiz.uz"
    eskiz_email: str | None = None
    eskiz_password: SecretStr | None = None
    eskiz_from: str = "4546"
    timeout_seconds: float = 5.0


class ConsoleSmsClient:
    """Lokal ishlab chiqish: SMS logga tushadi. `sent` — testlar uchun."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, phone: str, text: str) -> str:
        self.sent.append((phone, text))
        logger.warning("SMS (console) -> %s: %s", mask_phone(phone), text)
        return f"console-{len(self.sent)}"

    async def aclose(self) -> None:
        return None


class EskizSmsClient:
    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        password: str,
        sender: str,
        token_store: TokenStore | None = None,
        timeout_seconds: float = 5.0,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not email or not password:
            # Fail-closed: kalitsiz servis jim "SMS yuborildi" deb yolg'on gapirmasin.
            raise ValueError("Eskiz: ESKIZ_EMAIL va ESKIZ_PASSWORD majburiy")
        self._email = email
        self._password = password
        self._sender = sender
        self._tokens = token_store or MemoryTokenStore()
        self.breaker = breaker or CircuitBreaker("eskiz", failure_threshold=5, recovery_timeout=30.0)
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=httpx.Timeout(timeout_seconds), transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, phone: str, text: str) -> str:
        if not self.breaker.allow():
            raise SmsSendError("Eskiz: circuit breaker ochiq")
        try:
            response = await self._post_sms(phone, text, await self._token())
            if response.status_code == 401:
                response = await self._post_sms(phone, text, await self._token(force_refresh=True))
        except SmsSendError:
            self.breaker.record_failure()
            raise
        if response.status_code >= 500:
            self.breaker.record_failure()
            raise SmsSendError(f"Eskiz {response.status_code}")
        # 4xx — bitta xabar muammosi (raqam, shablon), provayder sog' — zanjir ochilmaydi.
        self.breaker.record_success()
        if response.status_code >= 400:
            raise SmsSendError(f"Eskiz {response.status_code}: {response.text[:300]}")
        try:
            message_id = response.json().get("id")
        except ValueError as exc:
            raise SmsSendError("Eskiz javobi JSON emas") from exc
        if not message_id:
            raise SmsSendError(f"Eskiz javobida id yo'q: {response.text[:300]}")
        return str(message_id)

    async def _post_sms(self, phone: str, text: str, token: str) -> httpx.Response:
        try:
            return await self._http.post(
                "/api/message/sms/send",
                headers={"Authorization": f"Bearer {token}"},
                # Eskiz raqamni "+" siz kutadi: 998901234567
                data={"mobile_phone": phone.lstrip("+"), "message": text, "from": self._sender},
            )
        except httpx.HTTPError as exc:
            raise SmsSendError(f"Eskiz'ga ulanib bo'lmadi: {type(exc).__name__}") from exc

    async def _token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = await self._tokens.get(ESKIZ_TOKEN_KEY)
            if cached:
                return cached
        try:
            response = await self._http.post("/api/auth/login", data={"email": self._email, "password": self._password})
        except httpx.HTTPError as exc:
            raise SmsSendError(f"Eskiz login: ulanib bo'lmadi: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise SmsSendError(f"Eskiz login {response.status_code}")
        try:
            token = (response.json().get("data") or {}).get("token")
        except ValueError as exc:
            raise SmsSendError("Eskiz login javobi JSON emas") from exc
        if not token:
            raise SmsSendError("Eskiz login javobida token yo'q")
        await self._tokens.set(ESKIZ_TOKEN_KEY, token, ESKIZ_TOKEN_TTL_SECONDS)
        return str(token)


def create_sms_client(
    config: SmsConfig,
    *,
    environment: str,
    token_store: TokenStore | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SmsClient:
    if config.provider == "console":
        if environment == "prod":
            raise ValueError("SMS_PROVIDER=console prod muhitida taqiqlangan")
        return ConsoleSmsClient()
    if config.provider == "eskiz":
        return EskizSmsClient(
            base_url=config.eskiz_base_url,
            email=config.eskiz_email or "",
            password=config.eskiz_password.get_secret_value() if config.eskiz_password else "",
            sender=config.eskiz_from,
            token_store=token_store,
            timeout_seconds=config.timeout_seconds,
            transport=transport,
        )
    raise ValueError(f"Noma'lum SMS_PROVIDER: {config.provider}")
