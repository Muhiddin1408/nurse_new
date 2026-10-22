"""/internal/v1 — search-service'ning internal API'si YO'Q (reestr: chaqiruvchilar —). Router xavfsiz standart
holatda: har qanday chaqiruv 403."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from medbron_common.errors import Forbidden

ALLOWED_CALLERS: tuple[str, ...] = ()


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(prefix="/internal/v1", tags=["internal"], dependencies=[Depends(_deny_all)])
