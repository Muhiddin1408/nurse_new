"""Eventlar → daromad daftari (ADR-0012). Har funksiya consumer tranzaksiyasi ichida, idempotent
(`UNIQUE(kind, source_ref)`) — takroriy event ikkinchi yozuv bermaydi.

Qoidalar:
- Earning: to'langan (`paid_tiyin > 0`) bron yakunlangan / mijoz kelmagan / tasdiqlangandan keyin bekor
  qilingan — shifokor ulushi `share_of(money, paid_tiyin)`. Klinikada to'langan (`paid_tiyin = 0`) bron
  daftarga tushmaydi: pul platformadan o'tmagan.
- Refund / reversal: −`doctor_share_tiyin` (payment-service ledger'i bilan bir xil son — qayta hisoblanmaydi).
  Bekor qilish jarimasi alohida yozuvsiz chiqadi: earning(paid) − refund ulushi = jarimaning shifokor ulushi.
- Tartib kafolatlanmagan: refund earning'dan oldin kelsa, shifokorsiz ("yetim") yoziladi va earning
  kelganda bron bo'yicha to'ldiriladi. Retry/DLQ o'rniga shunday — refund faqat to'langan bronga keladi,
  to'langan bronning yakuniy eventi (completed / no_show / cancelled) baribir keladi; hech qachon earning
  bo'lmaydigan bron (`confirmation_failed` refund'i) uchun yetim yozuv payout'ga umuman kirmaydi — to'g'ri,
  chunki shifokor u pulni ishlamagan.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.money import share_of

from app import metrics
from app.models.billing import EARNING, REFUND, REVERSAL
from app.repositories import entries as repo

logger = logging.getLogger(__name__)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def record_earning(
    session: AsyncSession, data: dict[str, Any], *, status: str, eligible_at: datetime, services: list[str]
) -> bool:
    paid = int(data["paid_tiyin"])
    if paid <= 0:
        return False
    share = share_of(data["money"], paid)
    booking_id = uuid.UUID(data["booking_id"])
    created = await repo.insert_entry(
        session,
        kind=EARNING,
        source_ref=str(booking_id),
        booking_id=booking_id,
        doctor_id=uuid.UUID(data["doctor_id"]),
        clinic_id=uuid.UUID(data["clinic_id"]) if data.get("clinic_id") else None,
        booking_number=data["number"],
        booking_status=status,
        start_at=_dt(data["start_at"]),
        services=services,
        amount_tiyin=share.doctor_tiyin,
        gross_tiyin=paid,
        platform_tiyin=share.platform_tiyin,
        provider_tiyin=share.provider_tiyin,
        eligible_at=eligible_at,
    )
    if created:
        metrics.ENTRIES.labels(kind=EARNING).inc()
        earning = await repo.earning_of(session, booking_id)
        if earning is not None:
            await repo.link_orphans(session, earning)
    return created


async def _record_deduction(
    session: AsyncSession, *, kind: str, source_ref: str, data: dict[str, Any], eligible_at: datetime
) -> bool:
    booking_id = uuid.UUID(data["booking_id"])
    amount, doctor_share = int(data["amount_tiyin"]), int(data["doctor_share_tiyin"])
    earning = await repo.earning_of(session, booking_id)
    created = await repo.insert_entry(
        session,
        kind=kind,
        source_ref=source_ref,
        booking_id=booking_id,
        doctor_id=earning.doctor_id if earning else None,
        clinic_id=earning.clinic_id if earning else None,
        booking_number=earning.booking_number if earning else "",
        booking_status=earning.booking_status if earning else "",
        start_at=earning.start_at if earning else None,
        services=[],
        amount_tiyin=-doctor_share,
        # Qaytgan summaning shifokordan tashqari qismi (provayder komissiyasi ham) — platforma hisobidan
        gross_tiyin=-amount,
        platform_tiyin=-(amount - doctor_share),
        provider_tiyin=0,
        eligible_at=eligible_at,
    )
    if created:
        metrics.ENTRIES.labels(kind=kind).inc()
        if earning is None:
            metrics.ORPHAN_ENTRIES.labels(kind=kind).inc()
            logger.info("deduction before booking event", extra={"booking_id": str(booking_id), "kind": kind})
    return created


async def record_refund(session: AsyncSession, data: dict[str, Any]) -> bool:
    return await _record_deduction(
        session, kind=REFUND, source_ref=data["refund_id"], data=data, eligible_at=_dt(data["completed_at"])
    )


async def record_reversal(session: AsyncSession, data: dict[str, Any]) -> bool:
    return await _record_deduction(
        session, kind=REVERSAL, source_ref=data["payment_id"], data=data, eligible_at=_dt(data["reversed_at"])
    )
