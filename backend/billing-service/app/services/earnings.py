"""Daromad paneli (D6): "qancha ishladim?" — davr bo'yicha bron qatorlari va jami.

Davr — qabul sanasi (Asia/Tashkent). `paid_out` — to'langan payout'larga kirgan qism, qolgani —
`awaiting_payout` (hisoblangan, lekin hali o'tkazilmagan yoki hali payout'ga kirmagan)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import PAID
from app.repositories import entries as entries_repo
from app.repositories import payouts as payouts_repo
from app.services.errors import InvalidPeriod
from app.services.payouts import booking_lines, day_start, local_today, totals

MAX_RANGE_DAYS = 366


def resolve_range(period: str | None, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = local_today()
    if period == "today":
        return today, today
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    if period == "month":
        return today.replace(day=1), today
    if period is None and date_from and date_to:
        if date_to < date_from or (date_to - date_from).days > MAX_RANGE_DAYS:
            raise InvalidPeriod()
        return date_from, date_to
    raise InvalidPeriod()


async def summary(
    session: AsyncSession,
    *,
    frm: date,
    to: date,
    doctor_id: uuid.UUID | None = None,
    clinic_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    rows = await entries_repo.for_period(
        session, start=day_start(frm), end=day_start(to + timedelta(days=1)), doctor_id=doctor_id, clinic_id=clinic_id
    )
    payout_ids = list({e.payout_id for e in rows if e.payout_id is not None})
    statuses = await payouts_repo.statuses(session, payout_ids)
    t = totals(rows)
    paid_out = sum(e.amount_tiyin for e in rows if e.payout_id is not None and statuses.get(e.payout_id) == PAID)
    doctors = {e.booking_id: e.doctor_id for e in rows if e.kind == "earning"}
    lines = booking_lines(rows)
    for line in lines:
        pid = line["payout_id"]
        line["payout_status"] = statuses.get(pid) if pid else None
        line["doctor_id"] = doctors.get(line["booking_id"])
    return {
        "period_from": frm,
        "period_to": to,
        "bookings_count": t.bookings_count,
        "gross_tiyin": t.gross_tiyin,
        "platform_fee_tiyin": t.platform_fee_tiyin,
        "provider_fee_tiyin": t.provider_fee_tiyin,
        "refunds_tiyin": t.refunds_tiyin,
        "net_tiyin": t.net_tiyin,
        "paid_out_tiyin": paid_out,
        "awaiting_payout_tiyin": t.net_tiyin - paid_out,
        "transactions": lines,
    }
