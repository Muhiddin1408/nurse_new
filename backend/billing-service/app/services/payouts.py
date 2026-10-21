"""Payout: haftalik hisoblash, to'langan deb belgilash, CSV ko'chirma (D6, ADR-0012).

Davr — o'tgan to'liq hafta (Du–Ya, Asia/Tashkent). Shifokorning payout'ga kirmagan, `eligible_at` davr
oxirigacha bo'lgan BARCHA yozuvlari (oldingi haftalardan o'tganlari ham) bitta payout'ga yig'iladi.
Sof summa admin sozlamasi `payout.min_amount_tiyin` dan kam yoki ≤ 0 bo'lsa — keyingi haftaga o'tadi.
Idempotent: `UNIQUE(doctor_id, period_start, period_end)` + yozuvlar SKIP LOCKED bilan qulflanadi.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction
from medbron_common.events import Events
from medbron_common.security import CurrentAdmin

from app import metrics
from app.events import outbox
from app.models import EarningEntry, Payout
from app.models.billing import EARNING, PAID
from app.repositories import entries as entries_repo
from app.repositories import payouts as repo
from app.services.deps import Deps
from app.services.errors import InvalidPeriod, PayoutNotFound

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Tashkent")


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def last_full_week(today: date | None = None) -> tuple[date, date]:
    today = today or local_today()
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=LOCAL_TZ)


@dataclass(frozen=True)
class Totals:
    bookings_count: int
    gross_tiyin: int
    platform_fee_tiyin: int
    provider_fee_tiyin: int
    refunds_tiyin: int
    net_tiyin: int


def totals(rows: Sequence[EarningEntry]) -> Totals:
    earnings = [e for e in rows if e.kind == EARNING]
    return Totals(
        bookings_count=len({e.booking_id for e in earnings}),
        gross_tiyin=sum(e.gross_tiyin for e in earnings),
        platform_fee_tiyin=sum(e.platform_tiyin for e in earnings),
        provider_fee_tiyin=sum(e.provider_tiyin for e in earnings),
        refunds_tiyin=-sum(e.amount_tiyin for e in rows if e.kind != EARNING),
        net_tiyin=sum(e.amount_tiyin for e in rows),
    )


async def build(session: AsyncSession, deps: Deps, period_end: date | None = None) -> list[Payout]:
    """`period_end` — yakshanba (bo'lmasa o'sha haftaning yakshanbasi). Standart — o'tgan to'liq hafta."""
    if period_end is None:
        start, end = last_full_week()
    else:
        end = period_end + timedelta(days=6 - period_end.weekday())
        if end >= local_today():
            raise InvalidPeriod("Davr hali tugamagan")
        start = end - timedelta(days=6)
    cutoff = day_start(end + timedelta(days=1))
    min_amount = int((await deps.settings.get())["payout"]["min_amount_tiyin"])

    async with transaction(session):
        doctors = await entries_repo.doctors_with_unpaid(session, cutoff)
    created: list[Payout] = []
    for doctor_id in doctors:
        # Har shifokor — o'z tranzaksiyasi: bittasining xatosi boshqalarnikini qaytarmaydi
        async with transaction(session):
            payout = await _build_one(session, doctor_id, start, end, cutoff, min_amount)
        if payout is not None:
            created.append(payout)
    logger.info("payouts built", extra={"count": len(created), "period_start": str(start), "period_end": str(end)})
    return created


async def _build_one(
    session: AsyncSession, doctor_id: uuid.UUID, start: date, end: date, cutoff: datetime, min_amount: int
) -> Payout | None:
    rows = await entries_repo.lock_unpaid(session, doctor_id, cutoff)
    t = totals(rows)
    if not rows or t.net_tiyin <= 0 or t.net_tiyin < min_amount:
        return None  # keyingi haftaga o'tadi
    payout = await repo.create(
        session,
        doctor_id=doctor_id,
        period_start=start,
        period_end=end,
        bookings_count=t.bookings_count,
        gross_tiyin=t.gross_tiyin,
        platform_fee_tiyin=t.platform_fee_tiyin,
        provider_fee_tiyin=t.provider_fee_tiyin,
        refunds_tiyin=t.refunds_tiyin,
        net_tiyin=t.net_tiyin,
    )
    if payout is None:
        return None  # shu davr uchun allaqachon hisoblangan; yangi yozuvlar keyingi haftaga
    await entries_repo.assign(session, [e.id for e in rows], payout.id)
    outbox.add_event(
        session,
        Events.PAYOUT_CALCULATED,
        aggregate_id=payout.id,
        data={
            "payout_id": payout.id,
            "doctor_id": doctor_id,
            "period_start": start,
            "period_end": end,
            "bookings_count": t.bookings_count,
            "gross_tiyin": t.gross_tiyin,
            "platform_fee_tiyin": t.platform_fee_tiyin,
            "provider_fee_tiyin": t.provider_fee_tiyin,
            "refunds_tiyin": t.refunds_tiyin,
            "net_tiyin": t.net_tiyin,
        },
    )
    metrics.PAYOUTS_CREATED.inc()
    return payout


async def mark_paid(
    session: AsyncSession, payout_id: uuid.UUID, *, admin: CurrentAdmin, bank_reference: str, ip: str | None
) -> Payout:
    """Bank o'tkazmasi qilindi. Idempotent: allaqachon `paid` — o'zgarishsiz qaytariladi."""
    async with transaction(session):
        payout = await repo.get(session, payout_id, for_update=True)
        if payout is None:
            raise PayoutNotFound()
        if payout.status == PAID:
            return payout
        payout.status = PAID
        payout.paid_at = datetime.now(UTC)
        payout.paid_by = admin.admin_id
        payout.bank_reference = bank_reference
        outbox.add_event(
            session,
            Events.PAYOUT_PAID,
            aggregate_id=payout.id,
            data={
                "payout_id": payout.id,
                "doctor_id": payout.doctor_id,
                "net_tiyin": payout.net_tiyin,
                "paid_at": payout.paid_at,
                "bank_reference": bank_reference,
                "paid_by": admin.admin_id,
            },
        )
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="payout.mark_paid",
            object_type="payout",
            object_id=payout.id,
            before={"status": "pending"},
            after={"status": PAID, "net_tiyin": payout.net_tiyin, "bank_reference": bank_reference},
            ip=ip,
        )
    metrics.PAYOUTS_PAID.inc()
    return payout


def som(tiyin: int) -> str:
    """CSV uchun: 12_500_050 -> "125000.50" (Excel son sifatida o'qiydi)."""
    sign = "-" if tiyin < 0 else ""
    whole, frac = divmod(abs(tiyin), 100)
    return f"{sign}{whole}.{frac:02d}"


def booking_lines(rows: Sequence[EarningEntry]) -> list[dict[str, Any]]:
    """Yozuvlar → bron bo'yicha qatorlar (earning + uning refund'lari)."""
    lines: dict[uuid.UUID, dict[str, Any]] = {}
    for e in rows:
        line = lines.setdefault(
            e.booking_id,
            {
                "booking_id": e.booking_id,
                "number": e.booking_number,
                "date": e.start_at.astimezone(LOCAL_TZ).date() if e.start_at else None,
                "services": [],
                "status": e.booking_status,
                "gross_tiyin": 0,
                "platform_fee_tiyin": 0,
                "provider_fee_tiyin": 0,
                "refunds_tiyin": 0,
                "net_tiyin": 0,
                "payout_id": None,
            },
        )
        if e.kind == EARNING:
            line.update(
                number=e.booking_number,
                status=e.booking_status,
                services=list(e.services),
                gross_tiyin=e.gross_tiyin,
                platform_fee_tiyin=e.platform_tiyin,
                provider_fee_tiyin=e.provider_tiyin,
                payout_id=e.payout_id,
            )
        else:
            line["refunds_tiyin"] -= e.amount_tiyin
        line["net_tiyin"] += e.amount_tiyin
    return list(lines.values())


def statement_csv(payout: Payout, rows: Sequence[EarningEntry]) -> str:
    """Excel ochadigan CSV (UTF-8 BOM, `;`). Bemor ma'lumoti yo'q — bron raqami va xizmatlar."""
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["MedBron — to'lov hisoboti"])
    w.writerow(["Shifokor", str(payout.doctor_id)])
    w.writerow(["Davr", payout.period_start.isoformat(), payout.period_end.isoformat()])
    w.writerow(["Holat", payout.status, payout.bank_reference])
    w.writerow([])
    w.writerow(["Sana", "Bron", "Xizmatlar", "Holat", "Summa", "Komissiya", "Provayder", "Qaytarilgan", "Sof"])
    for line in booking_lines(rows):
        w.writerow(
            [
                line["date"].isoformat() if line["date"] else "",
                line["number"],
                ", ".join(line["services"]),
                line["status"],
                som(line["gross_tiyin"]),
                som(line["platform_fee_tiyin"]),
                som(line["provider_fee_tiyin"]),
                som(line["refunds_tiyin"]),
                som(line["net_tiyin"]),
            ]
        )
    w.writerow([])
    w.writerow(
        [
            "JAMI",
            "",
            "",
            payout.bookings_count,
            som(payout.gross_tiyin),
            som(payout.platform_fee_tiyin),
            som(payout.provider_fee_tiyin),
            som(payout.refunds_tiyin),
            som(payout.net_tiyin),
        ]
    )
    return buf.getvalue()
