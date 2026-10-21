"""So'rov konteksti (contextvars) — log, chiquvchi HTTP va eventlarga uzatiladi."""

from __future__ import annotations

import ipaddress
from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()


def client_ip(request: object) -> str | None:
    """Mijoz IP manzili. Kong `X-Real-IP` ni o'zi qo'yadi (mijoz yuborgan qiymatni ustidan yozadi),
    shuning uchun u `X-Forwarded-For` dan ishonchli — XFF ning chap qismini mijoz soxtalashtira oladi."""
    headers = getattr(request, "headers", None)
    real_ip = headers.get("x-real-ip") if headers is not None else None
    client = getattr(request, "client", None)
    for candidate in (real_ip, getattr(client, "host", None)):
        if candidate:
            try:
                # Faqat haqiqiy IP: noto'g'ri qiymat (soxta header, test klienti) logga/bazaga tushmaydi.
                return str(ipaddress.ip_address(str(candidate).strip()))
            except ValueError:
                continue
    return None
