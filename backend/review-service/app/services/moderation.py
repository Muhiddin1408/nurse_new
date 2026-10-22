"""Avtomatik moderatsiya — sof funksiya (monolit `api/booking/reviews.py` qoidalari).

Maqsad hammasini ushlash emas, eng ko'p uchraydiganini `pending` ga tushirish — yakuniy qarorni odam qiladi.
Bayroqlar: `profanity` (so'kinish), `phone` (telefon raqami), `link` (havola / reklama / @username).
"""

from __future__ import annotations

import re

# Monolitda `\b` raqamlar orasida edi — eng ko'p uchraydigan "+998901234567" (bo'shliqsiz) USHLANMASDI.
# Chegara — "oldi/keyini raqam emas" (lookaround), shunda uzun raqamlar (summa, ID) bayroq bermaydi.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?998[\s\-()]*)?\d{2}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
_LINK_RE = re.compile(r"(https?://|www\.|t\.me/|@[A-Za-z0-9_]{4,}|\b[\w-]+\.(?:uz|com|ru|net|org)\b)", re.I)
_BAD_WORDS = (
    "ahmoq", "tentak", "iflos", "haromi", "jalab", "qanjiq", "itvachcha",
    "дурак", "идиот", "тупой", "сука", "бля", "урод", "мразь",
)  # fmt: skip
_BAD_RE = re.compile(r"\b(" + "|".join(map(re.escape, _BAD_WORDS)) + r")", re.I)


def flags(text: str) -> list[str]:
    """Bo'sh ro'yxat — toza."""
    found = []
    if _BAD_RE.search(text):
        found.append("profanity")
    if _PHONE_RE.search(text):
        found.append("phone")
    if _LINK_RE.search(text):
        found.append("link")
    return found


def short_name(full: str) -> str:
    """Ommaviy sahifada to'liq ism emas — "Aziz T." (shaxsiy ma'lumot)."""
    parts = full.split()
    if not parts:
        return ""
    return parts[0] + (f" {parts[1][0]}." if len(parts) > 1 else "")
