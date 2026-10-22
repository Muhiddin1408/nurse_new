"""/internal/v1/admin — faqat servislararo, `X-Internal-Token` bilan. Kong tashqariga OCHMAYDI.

Ruxsat etilgan chaqiruvchilar — docs/SERVICE_REGISTRY.md dagi ro'yxat bilan bir xil bo'lishi shart.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from fastapi.responses import JSONResponse

from medbron_common.errors import Forbidden

from app.core.config import get_settings
from app.core.security import verify_internal_token
from app.routers.deps import Session
from app.services import platform

ALLOWED_CALLERS: tuple[str, ...] = (
    "billing-service",
    "booking-service",
    "dispute-service",
    "payment-service",
    "pricing-service",
    "review-service",
    "schedule-service",
)


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Xavfsiz standart: ro'yxat bo'sh bo'lsa — hech kim chaqira olmaydi.
    dependencies=[Depends(verify_internal_token(*ALLOWED_CALLERS) if ALLOWED_CALLERS else _deny_all)],
)


@router.get("/settings", response_model=None)
async def settings(session: Session, if_none_match: Annotated[str | None, Header()] = None) -> Response:
    """Global sozlamalar. Chaqiruvchilar 60 s keshlaydi va `admin.settings_changed` da tashlaydi;
    `If-None-Match` mos bo'lsa — 304 (tarmoq va JSON tejaladi)."""
    snapshot = await platform.current(session)
    tag = platform.etag(snapshot.version)
    headers = {"ETag": tag, "Cache-Control": f"private, max-age={get_settings().settings_cache_max_age_seconds}"}
    if if_none_match == tag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return JSONResponse({"version": snapshot.version, "values": snapshot.values.model_dump()}, headers=headers)
