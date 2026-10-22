from __future__ import annotations

import base64
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip

from app.core.database import get_session
from app.models import Booking, BookingItem
from app.schemas.bookings import AppointmentOut, BookingOut, ItemOut
from app.services.deps import Deps
from app.services.policy import LOCAL_TZ

Session = Annotated[AsyncSession, Depends(get_session)]


def get_deps(request: Request) -> Deps:
    deps: Deps = request.app.state.deps
    return deps


def get_ip(request: Request) -> str | None:
    return client_ip(request)


DepsDep = Annotated[Deps, Depends(get_deps)]
Ip = Annotated[str | None, Depends(get_ip)]


def booking_out(b: Booking, items: list[BookingItem]) -> BookingOut:
    return BookingOut(
        id=b.id,
        number=b.number,
        status=b.status,
        place=b.place,
        start_at=b.start_at,
        end_at=b.end_at,
        doctor_id=b.doctor_id,
        clinic_id=b.clinic_id,
        patient=b.patient,
        address=b.address,
        items=[ItemOut.model_validate(i) for i in items],
        original_tiyin=b.original_tiyin,
        price_adjust_percent=b.price_adjust_percent,
        discount_kind=b.discount_kind,
        discount_tiyin=b.discount_tiyin,
        promo_code=b.promo_code,
        total_tiyin=b.total_tiyin,
        prepay_tiyin=b.prepay_tiyin,
        payment_mode=b.payment_mode,
        payment_due_at=b.payment_due_at,
        paid_tiyin=b.paid_tiyin,
        rescheduled_count=b.rescheduled_count,
        client_comment=b.client_comment,
        cancelled_by=b.cancelled_by,
        cancel_reason_code=b.cancel_reason_code,
        created_at=b.created_at,
    )


def appointment_out(b: Booking, items: list[BookingItem], now: datetime) -> AppointmentOut:
    same_day = b.start_at.astimezone(LOCAL_TZ).date() == now.astimezone(LOCAL_TZ).date()
    address = None
    if b.address:
        keys: tuple[str, ...] = ("city", "district", "street", "latitude", "longitude")
        if same_day:
            keys = (*keys, "entrance", "floor", "apartment", "comment")
        address = {k: b.address.get(k, "") for k in keys}
    to_collect = b.total_tiyin - b.prepay_tiyin if b.status in ("confirmed", "in_progress") else 0
    return AppointmentOut(
        id=b.id,
        number=b.number,
        status=b.status,
        place=b.place,
        doctor_id=b.doctor_id,
        start_at=b.start_at,
        end_at=b.end_at,
        clinic_id=b.clinic_id,
        patient=b.patient,
        services=[ItemOut.model_validate(i) for i in items],
        client_comment=b.client_comment,
        address=address,
        started_at=b.started_at,
        completed_at=b.completed_at,
        note=b.note,
        total_tiyin=b.total_tiyin,
        to_collect_tiyin=max(0, to_collect),
    )


def encode_cursor(b: Booking) -> str:
    return base64.urlsafe_b64encode(f"{b.created_at.isoformat()}|{b.id}".encode()).decode()


def decode_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if not value:
        return None
    try:
        created, ident = base64.urlsafe_b64decode(value.encode()).decode().split("|", 1)
        return datetime.fromisoformat(created), UUID(ident)
    except (ValueError, UnicodeDecodeError):
        return None
