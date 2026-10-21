"""So'rov konteksti middleware'i: X-Request-ID + HTTP metrikalari + kirish logi.

Sof ASGI (BaseHTTPMiddleware emas) — streaming javoblarni buzmaydi va contextvars
handler ichiga to'g'ri uzatiladi.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from medbron_common.context import request_id_var
from medbron_common.metrics import HTTP_DURATION, HTTP_REQUESTS

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SKIP_ACCESS_LOG = frozenset({"/health/live", "/health/ready", "/metrics"})

logger = logging.getLogger("medbron.access")


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "__unmatched__"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        # Begona/zararli qiymat logga va boshqa servislarga tarqalmasin.
        request_id = incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - started
            route = _route_template(scope)
            method = scope.get("method", "")
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_DURATION.labels(method, route).observe(elapsed)
            if scope.get("path") not in _SKIP_ACCESS_LOG:
                logger.info(
                    "%s %s %s",
                    method,
                    scope.get("path"),
                    status_code,
                    extra={"route": route, "status": status_code, "duration_ms": round(elapsed * 1000, 2)},
                )
            request_id_var.reset(token)
