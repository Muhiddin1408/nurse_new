"""Yagona xato formati (BLUEPRINT 5.3): `{"detail": {"code": "...", "message": "..."}}`."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Biznes xatosi. Servislar o'z xatolarini shundan meros qilib e'lon qiladi:

    class SlotTaken(Conflict):
        code = "SLOT_TAKEN"
        message = "Bu vaqt allaqachon band qilingan"
    """

    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "So'rov noto'g'ri"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.extra = extra or {}
        self.headers = headers
        super().__init__(self.message)

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


class BadRequest(AppError):
    pass


class Unauthorized(AppError):
    status_code = 401
    code = "UNAUTHORIZED"
    message = "Autentifikatsiya talab qilinadi"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "Ruxsat yo'q"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Topilmadi"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Hozirgi holatda bajarib bo'lmaydi"


class Unprocessable(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "Ma'lumotlar noto'g'ri"


class TooManyRequests(AppError):
    status_code = 429
    code = "TOO_MANY_REQUESTS"
    message = "Juda ko'p so'rov, keyinroq urinib ko'ring"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    message = "Servis vaqtincha mavjud emas"


def error_response(status_code: int, detail: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code, headers=headers)


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.error("app error", extra={"code": exc.code, "error": exc.message})
    return error_response(exc.status_code, exc.to_detail(), exc.headers)


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return error_response(422, {"code": "VALIDATION_ERROR", "message": "Ma'lumotlar noto'g'ri", "errors": errors})


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        detail = exc.detail
    else:
        detail = {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail)}
    return error_response(exc.status_code, detail, getattr(exc, "headers", None))


async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error", exc_info=exc)
    return error_response(500, {"code": "INTERNAL_ERROR", "message": "Ichki xato"})


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
