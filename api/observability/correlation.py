"""
observability/correlation.py — correlation_id butun zanjir bo'ylab (E13).

    HTTP so'rov -> middleware correlation_id beradi (yoki X-Correlation-ID dan oladi)
        -> loglarning har bir qatorida
        -> outbox hodisasida -> Kafka konvertida
        -> consumer loglarida
        -> xato javobida `trace_id` (mijoz support'ga ayta oladi)

"Nega bu mijozga SMS kelmadi?" — bitta ID bo'yicha API, outbox, Kafka va
consumer loglarini bog'lab topiladi.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import uuid
from contextlib import contextmanager

HEADER = "HTTP_X_CORRELATION_ID"
RESPONSE_HEADER = "X-Correlation-ID"
_VALID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_current: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return _current.get()


def new_correlation_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def correlation_scope(value: str | None):
    token = _current.set(value if value and _VALID.match(value) else new_correlation_id())
    try:
        yield _current.get()
    finally:
        _current.reset(token)


class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with correlation_scope(request.META.get(HEADER)) as cid:
            request.correlation_id = cid
            response = self.get_response(request)
            response[RESPONSE_HEADER] = cid
            return response


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id()
        return True


class JsonFormatter(logging.Formatter):
    """Strukturali log: har qator bitta JSON. Erkin matnli loglarni qidirib bo'lmaydi."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", ""),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
