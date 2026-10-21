"""Telegram Bot API — timeout, circuit breaker; xatoni yutmaydi (chaqiruvchi keyingi kanalga o'tadi)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from medbron_common.circuit import CircuitBreaker

logger = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self._token = token
        self._http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10.0, connect=3.0), transport=transport)
        self.breaker = CircuitBreaker("telegram", failure_threshold=5, recovery_timeout=60.0)

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._token:
            raise TelegramError("Telegram bot sozlanmagan")
        if not self.breaker.allow():
            raise TelegramError("Telegram: circuit breaker ochiq")
        try:
            resp = await self._http.post(f"/bot{self._token}/{method}", json=payload)
            data: dict[str, Any] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.breaker.record_failure()
            raise TelegramError(type(exc).__name__) from exc
        if not data.get("ok"):
            # 4xx (bot bloklangan, chat yo'q) — bitta chat muammosi, zanjir ochilmaydi
            if resp.status_code >= 500 or resp.status_code == 429:
                self.breaker.record_failure()
            raise TelegramError(str(data.get("description", "Telegram xatosi"))[:200])
        self.breaker.record_success()
        result: dict[str, Any] = data.get("result", {})
        return result

    async def send_message(self, chat_id: int, text: str) -> None:
        await self._call("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
