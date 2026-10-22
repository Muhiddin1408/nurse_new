"""ClickHouse hisobot so'rovlari (`events_raw FINAL` — dublikatlarsiz). Vaqt oynasi — UTC `[frm, to)`,
kun guruhlash — Asia/Tashkent. Shifokor/klinika filtri bron bo'yicha: `booking.created` dagi
`doctor_id`/`clinic_id` (to'lov/refund eventlarida ular yo'q — `booking_id` orqali bog'lanadi).
Barcha qiymatlar — server parametrlari (`{name:Type}`), SQL matniga qo'shilmaydi."""

# SQL matni faqat shu moduldagi konstantalar va `TOP_SORT` oq ro'yxatidan yig'iladi — foydalanuvchi qiymati emas
# ruff: noqa: S608

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.clients.clickhouse import ClickHouse

WINDOW = "occurred_at >= {frm:DateTime64(3, 'UTC')} AND occurred_at < {to:DateTime64(3, 'UTC')}"
SCOPE = (
    "({doctor:String} = '' AND {clinic:String} = '') OR booking_id IN ("
    "SELECT booking_id FROM events_raw FINAL WHERE event_type = 'booking.created'"
    " AND ({doctor:String} = '' OR doctor_id = {doctor:String})"
    " AND ({clinic:String} = '' OR clinic_id = {clinic:String}))"
)
BOOKING_TYPES = (
    "booking.created",
    "booking.confirmed",
    "booking.completed",
    "booking.cancelled",
    "booking.no_show",
    "booking.expired",
)
MONEY_TYPES = ("payment.captured", "refund.succeeded", "payment.reversed")
TOTALS = """
    countIf(event_type = 'booking.created')   AS created,
    countIf(event_type = 'booking.confirmed') AS confirmed,
    countIf(event_type = 'booking.completed') AS completed,
    countIf(event_type = 'booking.cancelled') AS cancelled,
    countIf(event_type = 'booking.no_show')   AS no_show,
    countIf(event_type = 'booking.expired')   AS expired,
    sumIf(JSONExtractInt(data, 'money', 'total_tiyin'), event_type = 'booking.completed') AS gmv_tiyin,
    sumIf(JSONExtractInt(data, 'amount_tiyin'), event_type = 'payment.captured') AS captured_tiyin,
    sumIf(JSONExtractInt(data, 'amount_tiyin'), event_type = 'refund.succeeded') AS refunded_tiyin,
    sumIf(JSONExtractInt(data, 'amount_tiyin'), event_type = 'payment.reversed') AS reversed_tiyin
"""
TOP_SORT = {"completed": "completed", "gmv": "gmv_tiyin", "no_show": "no_show", "cancelled": "cancelled"}


def _params(frm: datetime, to: datetime, doctor_id: UUID | None, clinic_id: UUID | None) -> dict[str, Any]:
    return {"frm": frm, "to": to, "doctor": str(doctor_id or ""), "clinic": str(clinic_id or "")}


def _ints(row: dict[str, Any]) -> dict[str, int]:
    return {k: int(v) for k, v in row.items()}  # JSON formatida UInt64/Int64 satr bo'lib keladi


async def totals(
    ch: ClickHouse, frm: datetime, to: datetime, *, doctor_id: UUID | None, clinic_id: UUID | None
) -> dict[str, int]:
    rows = await ch.select(
        f"SELECT {TOTALS} FROM events_raw FINAL WHERE {WINDOW} AND event_type IN {{types:Array(String)}} AND ({SCOPE})",
        {**_params(frm, to, doctor_id, clinic_id), "types": [*BOOKING_TYPES, *MONEY_TYPES]},
    )
    return _ints(rows[0])


async def daily(
    ch: ClickHouse, frm: datetime, to: datetime, *, doctor_id: UUID | None, clinic_id: UUID | None
) -> list[dict[str, Any]]:
    rows = await ch.select(
        f"SELECT toString(toDate(occurred_at, 'Asia/Tashkent')) AS day, {TOTALS} FROM events_raw FINAL"
        f" WHERE {WINDOW} AND event_type IN {{types:Array(String)}} AND ({SCOPE})"
        " GROUP BY day ORDER BY day",
        {**_params(frm, to, doctor_id, clinic_id), "types": [*BOOKING_TYPES, *MONEY_TYPES]},
    )
    return [{"day": r.pop("day"), **_ints(r)} for r in rows]


async def funnel(
    ch: ClickHouse, frm: datetime, to: datetime, *, doctor_id: UUID | None, clinic_id: UUID | None
) -> dict[str, int]:
    """Kogorta: oynada YARATILGAN bronlar; har bosqich — shu bronlardan nechtasi unga yetgan (istalgan vaqtda)."""
    rows = await ch.select(
        "WITH cohort AS ("
        "  SELECT booking_id, JSONExtractString(data, 'status') AS initial_status FROM events_raw FINAL"
        f"  WHERE event_type = 'booking.created' AND {WINDOW}"
        "    AND ({doctor:String} = '' OR doctor_id = {doctor:String})"
        "    AND ({clinic:String} = '' OR clinic_id = {clinic:String})"
        ")"
        " SELECT"
        "  (SELECT count() FROM cohort) AS created,"
        "  (SELECT countIf(initial_status = 'pending_payment') FROM cohort) AS needs_payment,"
        "  uniqExactIf(booking_id, event_type = 'payment.captured') AS paid,"
        "  uniqExactIf(booking_id, event_type = 'booking.confirmed') AS confirmed,"
        "  uniqExactIf(booking_id, event_type = 'booking.completed') AS completed,"
        "  uniqExactIf(booking_id, event_type = 'booking.cancelled') AS cancelled,"
        "  uniqExactIf(booking_id, event_type = 'booking.expired') AS expired,"
        "  uniqExactIf(booking_id, event_type = 'booking.no_show') AS no_show"
        " FROM events_raw FINAL"
        " WHERE booking_id IN (SELECT booking_id FROM cohort) AND event_type IN {types:Array(String)}",
        {**_params(frm, to, doctor_id, clinic_id), "types": [*BOOKING_TYPES, "payment.captured"]},
    )
    return _ints(rows[0])


async def top_doctors(
    ch: ClickHouse, frm: datetime, to: datetime, *, clinic_id: UUID | None, sort: str, limit: int
) -> list[dict[str, Any]]:
    order = TOP_SORT[sort]  # oq ro'yxat — identifikator parametr bo'la olmaydi
    rows = await ch.select(
        "SELECT doctor_id,"
        "  countIf(event_type = 'booking.completed') AS completed,"
        "  sumIf(JSONExtractInt(data, 'money', 'total_tiyin'), event_type = 'booking.completed') AS gmv_tiyin,"
        "  countIf(event_type = 'booking.no_show') AS no_show,"
        "  countIf(event_type = 'booking.cancelled') AS cancelled"
        " FROM events_raw FINAL"
        f" WHERE {WINDOW} AND event_type IN ('booking.completed', 'booking.no_show', 'booking.cancelled')"
        "   AND doctor_id != '' AND ({clinic:String} = '' OR clinic_id = {clinic:String})"
        f" GROUP BY doctor_id ORDER BY {order} DESC, doctor_id LIMIT {{limit:UInt32}}",
        {**_params(frm, to, None, clinic_id), "limit": limit},
    )
    return [{"doctor_id": r.pop("doctor_id"), **_ints(r)} for r in rows]
