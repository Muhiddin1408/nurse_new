"""Telegram Bot API klienti — timeout, circuit breaker, xatoni yutmaydi (chaqiruvchi fallback qiladi)."""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from api.notifications.circuit import CircuitBreaker

logger = logging.getLogger(__name__)

TIMEOUT = (3, 10)
_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)


class TelegramError(Exception):
    pass


def is_enabled() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN)


def _call(method: str, payload: dict) -> dict:
    if not is_enabled():
        raise TelegramError("Telegram bot sozlanmagan")
    if not _breaker.allow():
        raise TelegramError("Telegram: circuit breaker ochiq")
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        _breaker.record_failure()
        raise TelegramError(str(exc)) from exc
    if not data.get("ok"):
        # 4xx (masalan bot bloklangan) — breaker'ni ochmaydi, bu bitta chat muammosi
        if resp.status_code >= 500:
            _breaker.record_failure()
        raise TelegramError(data.get("description", "Telegram xatosi"))
    _breaker.record_success()
    return data.get("result", {})


def send_message(chat_id: int, text: str, *, buttons: list[list[dict]] | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return _call("sendMessage", payload)


def answer_callback(callback_id: str, text: str) -> None:
    try:
        _call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})
    except TelegramError:
        logger.warning("answerCallbackQuery yuborilmadi")
