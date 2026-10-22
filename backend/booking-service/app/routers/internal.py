"""/internal/v1/booking — faqat servislararo, `X-Internal-Token` bilan. Kong tashqariga OCHMAYDI.

Ruxsat etilgan chaqiruvchilar — docs/SERVICE_REGISTRY.md dagi ro'yxat bilan bir xil bo'lishi shart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from medbron_common.errors import Forbidden

from app.core.security import verify_internal_token
from app.repositories import bookings as repo
from app.routers.deps import DepsDep, Session
from app.services import lifecycle, payloads
from app.services.errors import BookingNotFound

ALLOWED_CALLERS: tuple[str, ...] = (
    "dispute-service",
    "payment-service",
    "review-service",
    "ws-gateway",
)


async def _deny_all() -> None:
    raise Forbidden(code="CALLER_NOT_ALLOWED", message="Bu servisning internal API'si chaqiruvchilarga ochilmagan")


router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    # Xavfsiz standart: ro'yxat bo'sh bo'lsa — hech kim chaqira olmaydi.
    dependencies=[Depends(verify_internal_token(*ALLOWED_CALLERS) if ALLOWED_CALLERS else _deny_all)],
)


@router.get("/bookings/{booking_id}/payable")
async def payable(booking_id: UUID, session: Session, deps: DepsDep) -> dict[str, Any]:
    """payment uchun: to'lash mumkinmi, qancha va fiskal chek elementlari (bo'sh kodlar — admin standartlari)."""
    b = await repo.get(session, booking_id)
    if b is None:
        raise BookingNotFound()
    fiscal = (await deps.settings.get())["fiscal"]
    now = datetime.now(UTC)
    return {
        "booking_id": str(b.id),
        "number": b.number,
        "status": b.status,
        "client_id": str(b.client_id),
        "doctor_id": str(b.doctor_id),
        "clinic_id": str(b.clinic_id) if b.clinic_id else None,
        "payment_mode": b.payment_mode,
        "prepay_tiyin": b.prepay_tiyin,
        "total_tiyin": b.total_tiyin,
        "payment_due_at": b.payment_due_at.isoformat() if b.payment_due_at else None,
        "payable": b.status == "pending_payment" and b.payment_due_at is not None and b.payment_due_at > now,
        # payment ledger'i to'lovni shu snapshot bo'yicha taqsimlaydi (qayta hisoblamaydi, ADR-0008/0012)
        "money": payloads.money(b),
        "items": [
            {
                "service_name": i.service_name,
                "price_tiyin": i.price_tiyin,
                "mxik_code": i.mxik_code or fiscal["default_mxik_code"],
                "package_code": i.package_code or fiscal["default_package_code"],
                "vat_percent": i.vat_percent if i.vat_percent is not None else fiscal["default_vat_percent"],
            }
            for i in await repo.items_of(session, b.id)
        ],
    }


@router.post("/bookings/{booking_id}/extend-payment")
async def extend_payment(booking_id: UUID, session: Session, deps: DepsDep) -> dict[str, Any]:
    b = await lifecycle.extend_payment(session, deps, booking_id)
    return {"payment_due_at": b.payment_due_at.isoformat() if b.payment_due_at else None}


@router.get("/bookings/{booking_id}/review-eligibility")
async def review_eligibility(booking_id: UUID, client_id: UUID, session: Session, deps: DepsDep) -> dict[str, Any]:
    b = await repo.get(session, booking_id)
    if b is None or b.client_id != client_id:
        return {"eligible": False, "reason": "not_owner", "doctor_id": None, "completed_at": None}
    if b.status != "completed" or b.completed_at is None:
        return {"eligible": False, "reason": "not_completed", "doctor_id": str(b.doctor_id), "completed_at": None}
    window = timedelta(days=int((await deps.settings.get())["sla"]["review_window_days"]))
    eligible = datetime.now(UTC) - b.completed_at <= window
    return {
        "eligible": eligible,
        "reason": "" if eligible else "window_closed",
        "doctor_id": str(b.doctor_id),
        "completed_at": b.completed_at.isoformat(),
    }


@router.get("/bookings/{booking_id}/summary")
async def summary(booking_id: UUID, user_id: UUID, session: Session) -> dict[str, Any]:
    b = await repo.get(session, booking_id)
    if b is None or b.client_id != user_id:
        raise BookingNotFound()
    return {
        "booking_id": str(b.id),
        "number": b.number,
        "status": b.status,
        "client_id": str(b.client_id),
        "doctor_id": str(b.doctor_id),
        "start_at": b.start_at.isoformat(),
        "paid_tiyin": b.paid_tiyin,
        "total_tiyin": b.total_tiyin,
    }


@router.get("/bookings/{booking_id}/authz")
async def authz(booking_id: UUID, user_id: UUID, session: Session, doctor_id: UUID | None = None) -> dict[str, bool]:
    b = await repo.get(session, booking_id)
    allowed = b is not None and (b.client_id == user_id or (doctor_id is not None and b.doctor_id == doctor_id))
    return {"allowed": allowed}
