"""
totp.py — RFC 6238 TOTP (A12, admin 2FA). Tashqi bog'liqliksiz, sof funksiyalar.

SMS emas, TOTP: SMS'ni SIM-swap bilan ushlab olish mumkin, admin akkaunti esa
butun bazaga kalit. Ilova: Google Authenticator, Authy, 1Password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)


def code_at(secret: str, step: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** DIGITS:0{DIGITS}d}"


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def verify(secret: str, code: str, *, now: float | None = None, last_step: int | None = None,
           window: int = 1) -> int | None:
    """To'g'ri bo'lsa — mos kelgan qadam (keyingi safar `last_step` sifatida saqlanadi,
    shu kodni QAYTA ishlatib bo'lmasin). Noto'g'ri bo'lsa — None.
    `window=1`: telefon soati ±30 soniya farq qilishi mumkin."""
    code = (code or "").strip()
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - window, step + window + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, account: str, issuer: str = "MedBron Admin") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}"
            f"?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP_SECONDS}")
