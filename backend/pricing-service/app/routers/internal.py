"""/internal/v1/pricing — faqat servislararo, `X-Internal-Token` bilan. Kong tashqariga OCHMAYDI.

Ruxsat etilgan chaqiruvchilar — docs/SERVICE_REGISTRY.md dagi ro'yxat bilan bir xil bo'lishi shart.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from medbron_common.errors import Forbidden

from app.core.security import verify_internal_token
from app.routers.deps import DepsDep, Session
from app.schemas.pricing import CommitRequest, InternalQuoteRequest
from app.services import quotes

ALLOWED_CALLERS: tuple[str, ...] = ("booking-service",)


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Xavfsiz standart: ro'yxat bo'sh bo'lsa — hech kim chaqira olmaydi.
    dependencies=[Depends(verify_internal_token(*ALLOWED_CALLERS) if ALLOWED_CALLERS else _deny_all)],
)


@router.post("/quotes")
async def create_quote(body: InternalQuoteRequest, session: Session, deps: DepsDep) -> dict[str, Any]:
    """Narx hisobi (10 daqiqa Redis'da). booking hold'dan OLDIN chaqiradi — noto'g'ri promo slotni band qilmaydi."""
    return await quotes.create(
        session,
        deps,
        quotes.QuoteRequest(
            client_id=body.client_id,
            doctor_id=body.doctor_id,
            service_ids=list(body.service_ids),
            slot_start_at=body.slot_start_at,
            price_adjust_percent=body.price_adjust_percent,
            place=body.place,
            promo_code=body.promo_code,
        ),
    )


@router.post("/quotes/{quote_id}/commit")
async def commit_quote(quote_id: UUID, body: CommitRequest, session: Session, deps: DepsDep) -> dict[str, Any]:
    """Promo/paket `reserved` band qilinadi (booking_id bo'yicha idempotent)."""
    return await quotes.commit(session, deps, quote_id, booking_id=body.booking_id, client_id=body.client_id)
