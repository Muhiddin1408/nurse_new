"""Kiruvchi eventlar (consumer tranzaksiyasi ichida, idempotent). Tarmoq chaqiruvi YO'Q — refund faqat
navbatga qo'yiladi, provayderga scheduler murojaat qiladi."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics import REFUND_REQUIRED, SAGA_COMPENSATIONS
from app.repositories import payments as repo
from app.services import ledger, refunds

logger = logging.getLogger(__name__)

# Refund.initiated_by (String(10)) — event aktorlari
_INITIATOR = {"client": "client", "doctor": "doctor", "clinic_admin": "clinic", "admin": "admin", "system": "system"}


async def on_booking_cancelled(session: AsyncSession, data: dict[str, Any]) -> None:
    """Bekor qilish siyosati natijasi (`refund_tiyin`) booking'da hisoblangan — bu yerda qayta hisoblanmaydi."""
    await refunds.request(
        session,
        booking_id=uuid.UUID(data["booking_id"]),
        amount_tiyin=int(data["refund_tiyin"]),
        source="booking_cancelled",
        source_ref=data["booking_id"],
        reason=data["reason_code"] or "booking_cancelled",
        initiated_by=_INITIATOR.get(data["cancelled_by"], "system"),
    )


async def on_booking_no_show(session: AsyncSession, data: dict[str, Any]) -> None:
    """`no_show_by=doctor` — mijozga to'liq (booking `refund_tiyin` beradi); mijoz kelmasa — 0, refund yo'q."""
    await refunds.request(
        session,
        booking_id=uuid.UUID(data["booking_id"]),
        amount_tiyin=int(data["refund_tiyin"]),
        source="booking_no_show",
        source_ref=data["booking_id"],
        reason=f"no_show_{data['no_show_by']}",
        initiated_by=_INITIATOR.get(data["marked_by"], "system"),
    )


async def on_confirmation_failed(session: AsyncSession, data: dict[str, Any]) -> None:
    """SAGA KOMPENSATSIYASI: pul yechildi, bron tasdiqlanmadi (muddati o'tgan / slot ketgan) — TO'LIQ refund."""
    payment_id = uuid.UUID(data["payment_id"])
    p = await repo.get(session, payment_id, for_update=True)
    if p is None:
        logger.error("confirmation_failed for unknown payment", extra={"payment_id": str(payment_id)})
        return
    if not p.needs_refund:
        p.needs_refund = True
        REFUND_REQUIRED.labels(provider=p.provider).inc()
        SAGA_COMPENSATIONS.labels(kind="confirmation_failed").inc()
        logger.error(
            "ALERT: money captured but booking not confirmed — refunding",
            extra={"payment_id": str(p.id), "booking_id": str(p.booking_id), "reason": data["reason"]},
        )
    await refunds.request(
        session,
        booking_id=p.booking_id,
        amount_tiyin=int(data["amount_tiyin"]),
        source="confirmation_failed",
        source_ref=str(payment_id),
        reason=data["reason"],
        initiated_by="system",
        payment_id=payment_id,
    )


async def on_dispute_resolved(session: AsyncSession, data: dict[str, Any]) -> None:
    """Nizo natijasi: `doctor_liable=false` — refundni to'liq platforma ko'taradi (shifokor ulushi 0, ADR-0012)."""
    await refunds.request(
        session,
        booking_id=uuid.UUID(data["booking_id"]),
        amount_tiyin=int(data["refund_tiyin"]),
        source="dispute",
        source_ref=data["dispute_id"],
        reason=f"dispute_{data['resolution']}",
        initiated_by="admin",
        doctor_liable=bool(data["doctor_liable"]),
    )


async def on_payout_paid(session: AsyncSession, data: dict[str, Any]) -> None:
    """billing bank o'tkazmasini belgiladi — daftarda doctor_payable yopiladi."""
    await ledger.post_payout_paid(session, data)
