"""Parollar (argon2id) va TOTP (RFC 6238; sir bazada Fernet bilan shifrlangan)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from functools import lru_cache

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_hasher = PasswordHasher()
# Login topilmaganda ham bir xil vaqt sarflanadi (login ro'yxatini taxmin qilib bo'lmasin).
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _verify_django_pbkdf2(encoded: str, password: str) -> bool:
    """Monolitdan ko'chirilgan `pbkdf2_sha256$<iter>$<salt>$<hash>` — birinchi kirishda argon2 ga o'tadi."""
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(base64.b64encode(digest).decode(), expected)


def verify_password(encoded: str | None, password: str) -> bool:
    if not encoded:
        _dummy_verify(password)
        return False
    if encoded.startswith("pbkdf2_sha256$"):
        return _verify_django_pbkdf2(encoded, password)
    try:
        return _hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _dummy_verify(password: str) -> None:
    try:
        _hasher.verify(_DUMMY_HASH, password + "\x00")
    except VerificationError:
        pass


def needs_rehash(encoded: str) -> bool:
    return encoded.startswith("pbkdf2_sha256$") or _hasher.check_needs_rehash(encoded)


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.totp_encryption_key is None:
        # Faqat local/test (Settings boshqa muhitda kalitsiz ishga tushmaydi).
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"local-only-totp-key").digest()))
    return Fernet(settings.totp_encryption_key.get_secret_value().encode())


def new_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("TOTP sirini ochib bo'lmadi — TOTP_ENCRYPTION_KEY o'zgarganmi?") from exc


def provisioning_uri(secret: str, login: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=login, issuer_name=get_settings().totp_issuer)


def match_totp_step(secret: str, code: str, *, at: float, last_step: int | None) -> int | None:
    """To'g'ri kod bo'lsa — uning 30 s qadami (±1 qadam soat farqiga yo'l qo'yiladi), aks holda None.
    Allaqachon ishlatilgan qadam (replay) rad etiladi."""
    totp = pyotp.TOTP(secret)
    current = int(at) // totp.interval
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None
