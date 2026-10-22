"""/v1/admin/analytics — admin JWT + `analytics.read`. Sanalar — Asia/Tashkent kunlari (`from`/`to` ham kiradi;
berilmasa — oxirgi 30 kun). Filtr: `doctor_id` / `clinic_id` (bron bo'yicha)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.clients.clickhouse import ClickHouse
from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.schemas.analytics import DayOut, FunnelOut, SummaryOut, TopDoctorOut
from app.services import reports

router = APIRouter(prefix="/v1/admin/analytics", tags=["admin"])

Reader = Annotated[CurrentAdmin, Depends(require_permission(permissions.ANALYTICS_READ))]
From = Annotated[date | None, Query(alias="from")]
To = Annotated[date | None, Query(alias="to")]


def get_clickhouse(request: Request) -> ClickHouse:
    ch: ClickHouse = request.app.state.clickhouse
    return ch


CH = Annotated[ClickHouse, Depends(get_clickhouse)]


@router.get("/summary", response_model=SummaryOut)
async def summary(
    _: Reader,
    ch: CH,
    date_from: From = None,
    date_to: To = None,
    doctor_id: UUID | None = None,
    clinic_id: UUID | None = None,
) -> dict[str, object]:
    return await reports.summary(ch, reports.resolve(date_from, date_to), doctor_id=doctor_id, clinic_id=clinic_id)


@router.get("/bookings/daily", response_model=list[DayOut])
async def bookings_daily(
    _: Reader,
    ch: CH,
    date_from: From = None,
    date_to: To = None,
    doctor_id: UUID | None = None,
    clinic_id: UUID | None = None,
) -> list[dict[str, object]]:
    return await reports.daily(ch, reports.resolve(date_from, date_to), doctor_id=doctor_id, clinic_id=clinic_id)


@router.get("/funnel", response_model=FunnelOut)
async def funnel(
    _: Reader,
    ch: CH,
    date_from: From = None,
    date_to: To = None,
    doctor_id: UUID | None = None,
    clinic_id: UUID | None = None,
) -> dict[str, object]:
    return await reports.funnel(ch, reports.resolve(date_from, date_to), doctor_id=doctor_id, clinic_id=clinic_id)


@router.get("/doctors/top", response_model=list[TopDoctorOut])
async def doctors_top(
    _: Reader,
    ch: CH,
    date_from: From = None,
    date_to: To = None,
    clinic_id: UUID | None = None,
    sort: Literal["completed", "gmv", "no_show", "cancelled"] = "completed",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, object]]:
    r = reports.resolve(date_from, date_to)
    return await reports.top_doctors(ch, r, clinic_id=clinic_id, sort=sort, limit=limit)
