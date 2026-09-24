"""
pii.py — shaxsiy ma'lumotni tarqatmaslik uchun yordamchilar (A10).

QOIDA: telefon raqami Kafka hodisasiga, analitikaga va logga TUSHMAYDI.
    * analitika kohortasi uchun — `client_hash` (HMAC, tuzsiz tiklab bo'lmaydi);
    * SMS yuborish uchun — consumer raqamni `booking_id` bo'yicha bazadan oladi;
    * logda — `mask_phone`.
"""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings


def client_hash(phone: str) -> str:
    """HMAC-SHA256(phone, ANALYTICS_SALT). Oddiy SHA-256 emas: O'zbekiston
    raqamlari fazosi kichik (~10^9), tuzsiz xeshni bir soatda teskari tiklash mumkin."""
    key = settings.ANALYTICS_SALT.encode()
    return hmac.new(key, phone.encode(), hashlib.sha256).hexdigest()[:32]


def mask_phone(phone: str) -> str:
    return phone[:-6] + "******" if len(phone) > 6 else "***"
