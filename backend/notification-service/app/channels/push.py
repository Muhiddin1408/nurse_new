"""Mobil push — Firebase Cloud Messaging HTTP v1 (Android va iOS ikkalasi FCM orqali).

OAuth2 tokeni service account JSON'idan (JWT bearer grant, RS256 — PyJWT) olinadi va ~55 daqiqa
keshlanadi; `google-auth` paketi kerak emas. Service account fayli — maxfiy (secrets/, repoga EMAS).

    PUSH_PROVIDER=console — logga (lokal);  fcm — FCM;  off — o'chiq (keyingi kanalga o'tadi).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
import jwt

from medbron_common.circuit import CircuitBreaker

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


class PushError(Exception):
    """Vaqtinchalik xato — chaqiruvchi keyingi kanalga o'tadi."""


class InvalidToken(PushError):
    """Token yaroqsiz (ilova o'chirilgan) — qurilma faolsizlantiriladi."""


class PushClient(Protocol):
    enabled: bool

    async def send(self, token: str, title: str, body: str, data: dict[str, str] | None = None) -> None: ...

    async def aclose(self) -> None: ...


class DisabledPush:
    enabled = False

    async def send(self, token: str, title: str, body: str, data: dict[str, str] | None = None) -> None:
        raise PushError("push o'chiq")

    async def aclose(self) -> None:
        return None


class ConsolePush:
    enabled = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send(self, token: str, title: str, body: str, data: dict[str, str] | None = None) -> None:
        self.sent.append((token, title, body))
        logger.info("PUSH (console) -> %s…: %s", token[:12], body)

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class ServiceAccount:
    project_id: str
    client_email: str
    private_key: str
    token_uri: str

    @classmethod
    def from_file(cls, path: Path, project_id: str | None = None) -> ServiceAccount:
        return cls.from_json(path.read_text(), project_id)

    @classmethod
    def from_json(cls, text: str, project_id: str | None = None) -> ServiceAccount:
        """JSON matni yoki uning base64'i."""
        stripped = text.strip()
        raw: dict[str, Any] = json.loads(stripped if stripped.startswith("{") else base64.b64decode(stripped))
        return cls(
            project_id=project_id or raw["project_id"],
            client_email=raw["client_email"],
            private_key=raw["private_key"],
            token_uri=raw.get("token_uri", "https://oauth2.googleapis.com/token"),
        )


class FcmPush:
    enabled = True

    def __init__(
        self,
        account: ServiceAccount,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._account = account
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0), transport=transport)
        self.breaker = breaker or CircuitBreaker("fcm", failure_threshold=5, recovery_timeout=60.0)
        self._token: str | None = None
        self._token_expires = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 300:
            return self._token
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self._account.client_email,
                "scope": FCM_SCOPE,
                "aud": self._account.token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            self._account.private_key,
            algorithm="RS256",
        )
        try:
            resp = await self._http.post(
                self._account.token_uri,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            )
        except httpx.HTTPError as exc:
            raise PushError(f"FCM oauth: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise PushError(f"FCM oauth {resp.status_code}")
        body = resp.json()
        self._token = str(body["access_token"])
        self._token_expires = time.time() + int(body.get("expires_in", 3600))
        return self._token

    async def send(self, token: str, title: str, body: str, data: dict[str, str] | None = None) -> None:
        if not self.breaker.allow():
            raise PushError("FCM: circuit breaker ochiq")
        url = f"https://fcm.googleapis.com/v1/projects/{self._account.project_id}/messages:send"
        message = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": {k: str(v) for k, v in (data or {}).items()},
                "android": {"priority": "high"},
                "apns": {"headers": {"apns-priority": "10"}},
            }
        }
        try:
            resp = await self._http.post(
                url, json=message, headers={"Authorization": f"Bearer {await self._access_token()}"}
            )
        except httpx.HTTPError as exc:
            self.breaker.record_failure()
            raise PushError(type(exc).__name__) from exc
        except PushError:
            self.breaker.record_failure()
            raise
        if resp.status_code in (400, 404) and ("UNREGISTERED" in resp.text or "INVALID_ARGUMENT" in resp.text):
            self.breaker.record_success()
            raise InvalidToken(resp.text[:200])
        if resp.status_code == 401:
            self._token = None  # token eskirgan — keyingi safar yangisi
        if resp.status_code >= 500 or resp.status_code in (401, 429):
            self.breaker.record_failure()
            raise PushError(f"FCM {resp.status_code}")
        if resp.status_code >= 400:
            raise PushError(f"FCM {resp.status_code}: {resp.text[:200]}")
        self.breaker.record_success()


def create_push_client(
    provider: str, credentials_file: Path | None, project_id: str | None, credentials_json: str | None = None
) -> PushClient:
    if provider == "fcm":
        if credentials_json:
            return FcmPush(ServiceAccount.from_json(credentials_json, project_id))
        assert credentials_file is not None
        return FcmPush(ServiceAccount.from_file(credentials_file, project_id))
    if provider == "console":
        return ConsolePush()
    return DisabledPush()
