"""Strukturali JSON log (BLUEPRINT 11): ts, level, service, request_id, trace_id, msg.

Shaxsiy ma'lumot (telefon, karta raqami) logga yozilishdan OLDIN maskalanadi —
kim qanday `logger.info(...)` chaqirganidan qat'i nazar.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from medbron_common.context import get_request_id

_PHONE_RE = re.compile(r"(?<!\d)(\+?998)(\d{2})(\d{3})(\d{4})(?!\d)")
_CARD_RE = re.compile(r"(?<!\d)(\d{12})(\d{4})(?!\d)")

_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys() | {"message", "asctime", "taskName"}
)


def mask_pii(text: str) -> str:
    """`+998901234567` → `+99890***4567`, 16 xonali karta → `************1234`."""
    text = _PHONE_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***{m.group(4)}", text)
    return _CARD_RE.sub(lambda m: "*" * 12 + m.group(2), text)


def _mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: _mask_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask_value(v) for v in value]
    return value


def _current_trace_id() -> str | None:
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover
        return None
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "request_id": get_request_id(),
            "trace_id": _current_trace_id(),
            "msg": mask_pii(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = _mask_value(value)
        if record.exc_info:
            payload["exc"] = mask_pii(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    # Kirish logini RequestContextMiddleware yozadi (request_id bilan) — uvicorn'niki takror.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("aio_pika").setLevel(logging.WARNING)
    logging.getLogger("aiormq").setLevel(logging.WARNING)
