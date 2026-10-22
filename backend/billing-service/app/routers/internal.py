"""/internal/v1/billing — faqat servislararo, `X-Internal-Token` bilan. Kong tashqariga OCHMAYDI.

Billing'ni hech bir servis sinxron chaqirmaydi (reestr: chaqiruvchilar —) — ro'yxat bo'sh, hamma rad etiladi.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from medbron_common.errors import Forbidden

from app.core.security import verify_internal_token

ALLOWED_CALLERS: tuple[str, ...] = ()


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(
    prefix="/internal/v1/billing",
    tags=["internal"],
    # Xavfsiz standart: ro'yxat bo'sh bo'lsa — hech kim chaqira olmaydi.
    dependencies=[Depends(verify_internal_token(*ALLOWED_CALLERS) if ALLOWED_CALLERS else _deny_all)],
)


@router.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "ok"}
