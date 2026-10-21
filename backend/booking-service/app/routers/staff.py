"""/v1/doctor/appointments (shifokor — JWT `doctor_id` claim) va /v1/clinic/{clinic_id}/appointments
(klinika admini — a'zolik catalog `/internal/v1/access` dan, Redis 60 s). To'lanmagan bronlar shifokorga
ko'rinmaydi; begona bron — 404."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from medbron_common.db import transaction
from medbron_common.errors import Forbidden

from app.core.redis import get_redis
from app.core.security import CurrentUser, get_current_user
from app.models import Booking
from app.repositories import bookings as repo
from app.routers.deps import DepsDep, Session, appointment_out
from app.schemas.bookings import AppointmentDetail, AppointmentOut, DoctorCancelIn, NoteIn
from app.services import lifecycle
from app.services.deps import Deps
from app.services.errors import BookingNotFound, InvalidBookingRequest
from app.services.policy import LOCAL_TZ

router = APIRouter(tags=["staff"])
User = Annotated[CurrentUser, Depends(get_current_user)]
DOCTOR_VISIBLE = ("confirmed", "in_progress", "completed", "no_show", "cancelled")


def _doctor_id(user: CurrentUser) -> UUID:
    if user.doctor_id is None:
        raise Forbidden(code="NOT_A_DOCTOR", message="Faqat shifokorlar uchun")
    return user.doctor_id


async def _own(session: Session, booking_id: UUID, doctor_id: UUID, *, for_update: bool = False) -> Booking:
    b = await repo.get(session, booking_id, for_update=for_update)
    if b is None or b.doctor_id != doctor_id or b.status not in DOCTOR_VISIBLE:
        raise BookingNotFound()
    return b


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=LOCAL_TZ)
    return start, start + timedelta(days=1)


@router.get("/v1/doctor/appointments/today", response_model=list[AppointmentOut])
async def today(user: User, session: Session) -> list[AppointmentOut]:
    now = datetime.now(UTC)
    start, end = _day_bounds(now.astimezone(LOCAL_TZ).date())
    rows = await repo.list_for_doctor(
        session, _doctor_id(user), start=start, end=end, statuses=("confirmed", "in_progress", "completed", "no_show")
    )
    items = await repo.items_for(session, [b.id for b in rows])
    return [appointment_out(b, items[b.id], now) for b in rows]


@router.get("/v1/doctor/appointments", response_model=list[AppointmentOut])
async def appointments(
    user: User,
    session: Session,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    status_: Annotated[str | None, Query(alias="status")] = None,
) -> list[AppointmentOut]:
    if date_to < date_from or (date_to - date_from).days > 31:
        raise InvalidBookingRequest("Oraliq noto'g'ri (≤ 31 kun)")
    statuses = tuple(s for s in status_.split(",") if s in DOCTOR_VISIBLE) if status_ else DOCTOR_VISIBLE
    start, _ = _day_bounds(date_from)
    _, end = _day_bounds(date_to)
    rows = await repo.list_for_doctor(session, _doctor_id(user), start=start, end=end, statuses=statuses)
    items = await repo.items_for(session, [b.id for b in rows])
    now = datetime.now(UTC)
    return [appointment_out(b, items[b.id], now) for b in rows]


@router.get("/v1/doctor/appointments/{booking_id}", response_model=AppointmentDetail)
async def appointment(booking_id: UUID, user: User, session: Session) -> AppointmentDetail:
    doctor_id = _doctor_id(user)
    b = await _own(session, booking_id, doctor_id)
    history = await repo.patient_history(session, doctor_id, b.patient_id)
    base = appointment_out(b, await repo.items_of(session, b.id), datetime.now(UTC))
    return AppointmentDetail(
        **base.model_dump(),
        patient_history=[
            {
                "booking_id": str(h.id),
                "number": h.number,
                "status": h.status,
                "start_at": h.start_at.isoformat(),
                "note": h.note,
            }
            for h in history
            if h.id != b.id
        ],
    )


@router.post("/v1/doctor/appointments/{booking_id}/start", response_model=AppointmentOut)
async def start(booking_id: UUID, user: User, session: Session) -> AppointmentOut:
    async with transaction(session):
        b = await _own(session, booking_id, _doctor_id(user), for_update=True)
        await lifecycle.start(session, b)
    return appointment_out(b, await repo.items_of(session, b.id), datetime.now(UTC))


@router.post("/v1/doctor/appointments/{booking_id}/complete", response_model=AppointmentOut)
async def complete(booking_id: UUID, body: NoteIn, user: User, session: Session) -> AppointmentOut:
    async with transaction(session):
        b = await _own(session, booking_id, _doctor_id(user), for_update=True)
        await lifecycle.complete(session, b, by="doctor", note=body.note, actor_id=user.user_id)
    return appointment_out(b, await repo.items_of(session, b.id), datetime.now(UTC))


@router.post("/v1/doctor/appointments/{booking_id}/no-show", response_model=AppointmentOut)
async def no_show(booking_id: UUID, body: NoteIn, user: User, session: Session) -> AppointmentOut:
    """Shifokor faqat mijoz kelmaganini belgilaydi (shifokor kelmasa — mijoz nizo ochadi, monolit C1)."""
    async with transaction(session):
        b = await _own(session, booking_id, _doctor_id(user), for_update=True)
        await lifecycle.mark_no_show(session, b, absent="client", by="doctor", note=body.note, actor_id=user.user_id)
    return appointment_out(b, await repo.items_of(session, b.id), datetime.now(UTC))


@router.post("/v1/doctor/appointments/{booking_id}/cancel", response_model=AppointmentOut)
async def doctor_cancel(
    booking_id: UUID, body: DoctorCancelIn, user: User, session: Session, deps: DepsDep
) -> AppointmentOut:
    """Shifokor bekor qilsa — mijozga har doim 100% (jadval o'zgarishida ham shu yo'l, ADR-0011)."""
    platform = await deps.settings.get()
    async with transaction(session):
        b = await _own(session, booking_id, _doctor_id(user), for_update=True)
        await lifecycle.cancel(
            session,
            b,
            actor="doctor",
            reason_code=None,
            reason_text=body.reason,
            platform=platform,
            actor_id=user.user_id,
        )
    return appointment_out(b, await repo.items_of(session, b.id), datetime.now(UTC))


# --- klinika ---------------------------------------------------------------------------------


async def _require_clinic_admin(request: Request, deps: Deps, user: CurrentUser, clinic_id: UUID) -> None:
    key = f"access:{user.user_id}:{clinic_id}"
    redis = get_redis() if request.app.state.has_redis else None
    cached = await redis.get(key) if redis is not None else None
    if cached is not None:
        allowed = json.loads(cached)
    else:
        allowed = bool((await deps.catalog.access(user.user_id, clinic_id=clinic_id))["allowed"])
        if redis is not None:
            await redis.set(key, json.dumps(allowed), ex=60)
    if not allowed:
        raise BookingNotFound("Klinika topilmadi", code="CLINIC_NOT_FOUND")


@router.get("/v1/clinic/{clinic_id}/appointments", response_model=list[AppointmentOut])
async def clinic_appointments(
    clinic_id: UUID,
    request: Request,
    user: User,
    session: Session,
    deps: DepsDep,
    day: Annotated[date | None, Query(alias="date")] = None,
    q: Annotated[str | None, Query(max_length=64)] = None,
) -> list[AppointmentOut]:
    await _require_clinic_admin(request, deps, user, clinic_id)
    now = datetime.now(UTC)
    start, end = _day_bounds(day or now.astimezone(LOCAL_TZ).date())
    rows = await repo.list_for_clinic(session, clinic_id, start=start, end=end, q=q)
    items = await repo.items_for(session, [b.id for b in rows])
    return [appointment_out(b, items[b.id], now) for b in rows]
