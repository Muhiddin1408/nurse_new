"""
notifications/push.py — mobil push (C13). Firebase Cloud Messaging HTTP v1.

    PUSH_PROVIDER=console  — logga (lokal, default)
    PUSH_PROVIDER=fcm      — FCM; `FCM_PROJECT_ID` va `FCM_CREDENTIALS_FILE`
                              (service account JSON, `google-auth` paketi)

Xato bo'lsa `PushError` — chaqiruvchi keyingi kanalga (Telegram / SMS) o'tadi.
Token yaroqsiz (ilova o'chirilgan) — `InvalidToken`: qurilma faolsizlantiriladi.
"""

from __future__ import annotations

import logging

from django.conf import settings

from api.notifications.circuit import CircuitBreaker

logger = logging.getLogger(__name__)

_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
_credentials = None


class PushError(Exception):
    pass


class InvalidToken(PushError):
    pass


def is_enabled() -> bool:
    return settings.PUSH_PROVIDER in ("console", "fcm")


def _access_token() -> str:
    global _credentials
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:  # paket o'rnatilmagan — push o'chiq, SMS'ga tushadi
        raise PushError("google-auth o'rnatilmagan") from exc
    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(
            settings.FCM_CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    if not _credentials.valid:
        _credentials.refresh(Request())
    return _credentials.token


def send(token: str, title: str, body: str, data: dict | None = None) -> None:
    if settings.PUSH_PROVIDER == "console":
        logger.info("PUSH (console) -> %s…: %s", token[:12], body)
        return
    if settings.PUSH_PROVIDER != "fcm":
        raise PushError("Push o'chiq")
    if not _breaker.allow():
        raise PushError("FCM: circuit breaker ochiq")

    import requests

    url = f"https://fcm.googleapis.com/v1/projects/{settings.FCM_PROJECT_ID}/messages:send"
    message = {"message": {"token": token, "notification": {"title": title, "body": body},
                           "data": {k: str(v) for k, v in (data or {}).items()}}}
    try:
        resp = requests.post(url, json=message, timeout=(3, 10),
                             headers={"Authorization": f"Bearer {_access_token()}"})
    except requests.RequestException as exc:
        _breaker.record_failure()
        raise PushError(str(exc)) from exc
    if resp.status_code in (400, 404) and ("UNREGISTERED" in resp.text or "INVALID_ARGUMENT" in resp.text):
        _breaker.record_success()
        raise InvalidToken(resp.text[:200])
    if resp.status_code >= 500:
        _breaker.record_failure()
        raise PushError(f"FCM {resp.status_code}")
    if resp.status_code >= 400:
        raise PushError(f"FCM {resp.status_code}: {resp.text[:200]}")
    _breaker.record_success()
