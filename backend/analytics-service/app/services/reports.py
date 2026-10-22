"""Hisobotlar: oraliq (Asia/Tashkent kunlari) → UTC oyna, ko'rsatkichlar va nisbatlar.

- `conversion_rate`  — oynada yaratilgan bronlardan tasdiqlanganlari ulushi (kogorta, funnel bilan bir xil);
- `cancellation_rate` — shu kogortadan bekor qilinganlari;
- `gmv_tiyin` — oynada yakunlangan qabullarning `money.total_tiyin` yig'indisi (klinikada to'langanlari ham);
- `net_payments_tiyin` — onlayn: yechilgan − qaytarilgan − provayder qaytargan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from medbron_common.errors import BadRequest

from app.clients.clickhouse import ClickHouse
from app.core.config import get_settings
from app.repositories import analytics as repo

LOCAL_TZ = ZoneInfo("Asia/Tashkent")
DEFAULT_DAYS = 30


class InvalidRange(BadRequest):
    code = "INVALID_RANGE"


@dataclass(frozen=True)
class Range:
    date_from: date
    date_to: date  # shu kun ham kiradi

    @property
    def utc_window(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self.date_from, time.min, tzinfo=LOCAL_TZ)
        end = datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
        return start.astimezone(UTC), end.astimezone(UTC)

    def days(self) -> list[date]:
        return [self.date_from + timedelta(days=i) for i in range((self.date_to - self.date_from).days + 1)]


def resolve(date_from: date | None, date_to: date | None, *, today: date | None = None) -> Range:
    today = today or datetime.now(LOCAL_TZ).date()
    to = date_to or today
    frm = date_from or to - timedelta(days=DEFAULT_DAYS - 1)
    if frm > to:
        raise InvalidRange("from > to")
    if (to - frm).days + 1 > get_settings().report_max_days:
        raise InvalidRange(f"Oraliq {get_settings().report_max_days} kundan oshmasin")
    return Range(frm, to)


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


async def summary(ch: ClickHouse, r: Range, *, doctor_id: UUID | None, clinic_id: UUID | None) -> dict[str, Any]:
    frm, to = r.utc_window
    totals = await repo.totals(ch, frm, to, doctor_id=doctor_id, clinic_id=clinic_id)
    cohort = await repo.funnel(ch, frm, to, doctor_id=doctor_id, clinic_id=clinic_id)
    net = totals["captured_tiyin"] - totals["refunded_tiyin"] - totals["reversed_tiyin"]
    return {
        "date_from": r.date_from,
        "date_to": r.date_to,
        **totals,
        "net_payments_tiyin": net,
        "conversion_rate": _rate(cohort["confirmed"], cohort["created"]),
        "cancellation_rate": _rate(cohort["cancelled"], cohort["created"]),
        "no_show_rate": _rate(cohort["no_show"], cohort["created"]),
    }


async def daily(ch: ClickHouse, r: Range, *, doctor_id: UUID | None, clinic_id: UUID | None) -> list[dict[str, Any]]:
    frm, to = r.utc_window
    by_day = {row["day"]: row for row in await repo.daily(ch, frm, to, doctor_id=doctor_id, clinic_id=clinic_id)}
    empty = dict.fromkeys(
        (
            "created",
            "confirmed",
            "completed",
            "cancelled",
            "no_show",
            "expired",
            "gmv_tiyin",
            "captured_tiyin",
            "refunded_tiyin",
            "reversed_tiyin",
        ),
        0,
    )
    out = []
    for d in r.days():  # bo'sh kunlar ham (grafik uchun uzluksiz)
        row = by_day.get(d.isoformat(), {"day": d.isoformat(), **empty})
        out.append(
            {
                **row,
                "day": d,
                "net_payments_tiyin": row["captured_tiyin"] - row["refunded_tiyin"] - row["reversed_tiyin"],
            }
        )
    return out


async def funnel(ch: ClickHouse, r: Range, *, doctor_id: UUID | None, clinic_id: UUID | None) -> dict[str, Any]:
    frm, to = r.utc_window
    f = await repo.funnel(ch, frm, to, doctor_id=doctor_id, clinic_id=clinic_id)
    return {"date_from": r.date_from, "date_to": r.date_to, **f, "conversion_rate": _rate(f["confirmed"], f["created"])}


async def top_doctors(
    ch: ClickHouse, r: Range, *, clinic_id: UUID | None, sort: str, limit: int
) -> list[dict[str, Any]]:
    frm, to = r.utc_window
    return await repo.top_doctors(ch, frm, to, clinic_id=clinic_id, sort=sort, limit=limit)
