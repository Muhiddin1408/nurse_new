"""E2E: ishlab turgan stack (backend/infra + ko'chirilgan servislar + Kong) ga qarshi.

    E2E_BASE_URL=http://localhost:8000  (Kong)          — standart
    test_booking_flow — hali ko'chirilmagan servis eventini `docker compose exec booking-service` orqali yuboradi
    E2E_ADMIN_LOGIN / E2E_ADMIN_PASSWORD / E2E_ADMIN_TOTP_SECRET — bootstrap admin (scripts/e2e.sh beradi)

Testlar o'z ma'lumotlarini yaratadi (tasodifiy telefon raqamlar) va bir-biriga bog'liq emas.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pyotp
import pytest

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def http() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE, timeout=15) as client:
        yield client


def random_phone() -> str:
    return f"+99890{random.randint(1_000_000, 9_999_999)}"  # noqa: S311


def eventually(check: Callable[[], Any], timeout: float = 30.0, interval: float = 0.5) -> Any:
    """Asinxron (event orqali) natijani kutadi: `check` xato bermay qaytarguncha."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return check()
        except AssertionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(interval)


@pytest.fixture(scope="session")
def login(http: httpx.Client) -> Callable[..., dict[str, Any]]:
    def do(phone: str | None = None) -> dict[str, Any]:
        phone = phone or random_phone()
        r = http.post("/v1/auth/otp/request", json={"phone": phone})
        if r.status_code == 429 and r.json()["detail"]["code"] == "OTP_COOLDOWN":
            # Shu raqam bilan qayta kirish (masalan, o'chirilgandan keyin) — servis cooldown'ini kutamiz
            time.sleep(int(r.json()["detail"]["retry_after"]) + 1)
            r = http.post("/v1/auth/otp/request", json={"phone": phone})
        assert r.status_code == 200, r.text
        code = r.json()["debug_code"]  # lokal stack: SMS_PROVIDER=console + OTP_DEBUG_ECHO
        r = http.post("/v1/auth/otp/verify", json={"phone": phone, "code": code})
        assert r.status_code == 200, r.text
        return {**r.json(), "phone": phone}

    return do


@pytest.fixture(scope="session")
def admin_token(http: httpx.Client) -> str:
    login_name = os.environ["E2E_ADMIN_LOGIN"]
    totp = pyotp.TOTP(os.environ["E2E_ADMIN_TOTP_SECRET"])
    r = http.post("/v1/admin/auth/login", json={"login": login_name, "password": os.environ["E2E_ADMIN_PASSWORD"]})
    assert r.status_code == 200, r.text
    r = http.post("/v1/admin/auth/totp", json={"mfa_token": r.json()["mfa_token"], "code": totp.now()})
    assert r.status_code == 200, r.text
    return str(r.json()["access"])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
