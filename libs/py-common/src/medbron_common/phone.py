"""Telefon raqami: normalizatsiya va maskalash (auth, notification, ko'chirish skriptlari).

Faqat O'zbekiston raqamlari (+998XXXXXXXXX): SMS pumping'dan himoya — hujumchi xalqaro premium
raqamlarga OTP so'ratib provayder pulini yoqadi (monolit A14).
"""

from __future__ import annotations

import re

UZ_PHONE_RE = re.compile(r"\+998\d{9}")


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """`+998 99 756-71-95` / `998997567195` → `+998997567195`. Normalizatsiyasiz bitta odam
    bazada bir necha marta paydo bo'ladi."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"+{digits}"


def normalize_uz_phone(raw: str) -> str:
    phone = normalize_phone(raw)
    if not UZ_PHONE_RE.fullmatch(phone):
        raise InvalidPhone("Faqat O'zbekiston raqamlari (+998XXXXXXXXX) qabul qilinadi")
    return phone


def mask_phone(phone: str) -> str:
    """`+998901234567` → `+99890***4567` (log formati bilan bir xil, ARCHITECTURE 10)."""
    if len(phone) < 9:
        return "***"
    return f"{phone[:6]}***{phone[-4:]}"
