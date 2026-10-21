"""/v1/bookings va /v1/waitlist — mijoz (user JWT). Begona bron — 404 (A4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import JSONResponse

from medbron_common.db import transaction

from app.core.security import CurrentUser, get_current_user
from app.models import Booking, WaitlistEntry
from app.repositories import bookings as repo
from app.routers.deps import DepsDep, Session, booking_out, decode_cursor, encode_cursor
from app.schemas.bookings import (
    BookingOut,
    BookingPage,
    CancelIn,
    CancellationPreviewOut,
    CreateBookingIn,
    RebookOut,
    RescheduleIn,
    WaitlistIn,
    WaitlistOut,
)
from app.services import lifecycle
from app.services.create import CreateRequest, create_booking
from app.services.errors import BookingNotFound, DoctorNotBookable, InvalidBookingRequest
from app.services.policy import LOCAL_TZ

router = APIRouter(tags=["bookings"])
User = Annotated[CurrentUser, Depends(get_current_user)]
STATUSES = {"pending_payment", "confirmed", "in_progress", "completed", "cancelled", "expired", "no_show"}


async def _owned(session: Session, booking_id: UUID, user: CurrentUser, *, for_update: bool = False) -> Booking:
    b = await repo.get(session, booking_id, for_update=for_update)
    if b is None or b.client_id != user.user_id:
        raise BookingNotFound()
    return b


@router.post("/v1/bookings", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateBookingIn,
    user: User,
    session: Session,
    deps: DepsDep,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=64)] = None,
) -> JSONResponse:
    """Bron yaratish (saga, ADR-0011). `Idempotency-Key` — tavsiya etiladi: takror so'rov o'sha bronni 200 bilan
    qaytaradi; boshqa tana bilan — 422. Javob: 201 (yangi) / 200 (takror)."""
    result = await create_booking(
        session,
        deps,
        CreateRequest(
            client_id=user.user_id,
            patient_id=body.patient_id,
            doctor_id=body.doctor_id,
            slot_id=body.slot_id,
            service_ids=body.service_ids,
            address_id=body.address_id,
            comment=body.comment,
            promo_code=body.promo_code,
            idempotency_key=idempotency_key,
        ),
    )
    out = booking_out(result.booking, await repo.items_of(session, result.booking.id))
    return JSONResponse(out.model_dump(mode="json"), status_code=200 if result.replayed else 201)


@router.get("/v1/bookings/mine", response_model=BookingPage)
async def mine(
    user: User,
    session: Session,
    status_: Annotated[str | None, Query(alias="status")] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> BookingPage:
    statuses = [s for s in status_.split(",") if s] if status_ else None
    if statuses and not set(statuses) <= STATUSES:
        raise InvalidBookingRequest("Noma'lum status")
    rows = await repo.list_for_client(
        session,
        user.user_id,
        statuses=statuses,
        start_from=date_from,
        start_to=date_to,
        cursor=decode_cursor(cursor),
        limit=limit,
    )
    items = await repo.items_for(session, [b.id for b in rows])
    return BookingPage(
        results=[booking_out(b, items[b.id]) for b in rows],
        next_cursor=encode_cursor(rows[-1]) if len(rows) == limit else None,
    )


@router.get("/v1/bookings/{booking_id}", response_model=BookingOut)
async def detail(booking_id: UUID, user: User, session: Session) -> BookingOut:
    b = await _owned(session, booking_id, user)
    return booking_out(b, await repo.items_of(session, b.id))


@router.get("/v1/bookings/{booking_id}/cancellation-preview", response_model=CancellationPreviewOut)
async def cancellation_preview(booking_id: UUID, user: User, session: Session, deps: DepsDep) -> CancellationPreviewOut:
    b = await _owned(session, booking_id, user)
    outcome = lifecycle.cancellation_preview(b, actor="client", platform=await deps.settings.get())
    return CancellationPreviewOut(**outcome.__dict__)


@router.post("/v1/bookings/{booking_id}/cancel", response_model=BookingOut)
async def cancel(booking_id: UUID, body: CancelIn, user: User, session: Session, deps: DepsDep) -> BookingOut:
    platform = await deps.settings.get()
    async with transaction(session):
        b = await _owned(session, booking_id, user, for_update=True)
        await lifecycle.cancel(
            session,
            b,
            actor="client",
            reason_code=None,
            reason_text=body.reason,
            platform=platform,
            actor_id=user.user_id,
        )
    return booking_out(b, await repo.items_of(session, b.id))


@router.post("/v1/bookings/{booking_id}/reschedule", response_model=BookingOut)
async def reschedule(booking_id: UUID, body: RescheduleIn, user: User, session: Session, deps: DepsDep) -> BookingOut:
    b = await lifecycle.reschedule(session, deps, booking_id, user.user_id, body.new_slot_id)
    return booking_out(b, await repo.items_of(session, b.id))


@router.get("/v1/bookings/{booking_id}/rebook", response_model=RebookOut)
async def rebook(booking_id: UUID, user: User, session: Session, deps: DepsDep) -> RebookOut:
    """Yana bron qilish: o'sha shifokor/xizmatlar/bemor, hozir mavjud xizmatlar va ≤ 5 bo'sh vaqt.
    Hech narsa yaratmaydi."""
    b = await _owned(session, booking_id, user)
    items = await repo.items_of(session, b.id)
    doctor = await deps.catalog.bookability(b.doctor_id)
    bookable = bool(doctor and doctor["bookable"])
    services = await deps.catalog.bookable_services(b.doctor_id, [i.service_id for i in items]) if bookable else []
    minutes = sum(int(s["duration_minutes"]) for s in services) or sum(i.duration_minutes for i in items)
    slots = []
    if bookable:
        now = datetime.now(UTC)
        found = await deps.schedule.free_slots(
            b.doctor_id, start=now, end=now + timedelta(days=14), place=b.place, min_minutes=minutes, limit=5
        )
        slots = [
            {"slot_id": str(s.id), "start_at": s.start_at.isoformat()}
            for s in found
            if b.clinic_id is None or s.clinic_id == b.clinic_id
        ]
    return RebookOut(
        doctor_id=b.doctor_id,
        doctor_bookable=bookable,
        place=b.place,
        clinic_id=b.clinic_id,
        patient_id=b.patient_id,
        address_id=b.address_id,
        service_ids=[UUID(s["id"]) for s in services],
        slots=slots,
    )


# --- waitlist --------------------------------------------------------------------------------


@router.get("/v1/waitlist", response_model=list[WaitlistOut])
async def waitlist(user: User, session: Session) -> list[WaitlistOut]:
    return [WaitlistOut.model_validate(e) for e in await repo.waitlist_of(session, user.user_id)]


@router.post("/v1/waitlist", response_model=WaitlistOut, status_code=status.HTTP_201_CREATED)
async def join_waitlist(body: WaitlistIn, user: User, session: Session, deps: DepsDep) -> WaitlistOut:
    """Bo'sh vaqt chiqsa xabar beriladi (band qilinmaydi). Shu shifokorga mavjud yozuv yangilanadi."""
    today = datetime.now(LOCAL_TZ).date()
    if body.date_to < body.date_from or body.date_from < today:
        raise InvalidBookingRequest("Sana oralig'i noto'g'ri")
    doctor = await deps.catalog.bookability(body.doctor_id)
    if doctor is None or not doctor["bookable"]:
        raise DoctorNotBookable()
    async with transaction(session):
        entry = await repo.waitlist_live(session, user.user_id, body.doctor_id)
        if entry is None:
            entry = WaitlistEntry(client_id=user.user_id, doctor_id=body.doctor_id)
            session.add(entry)
        entry.date_from, entry.date_to, entry.place = body.date_from, body.date_to, body.place
        entry.status, entry.notified_count, entry.notified_at = "active", 0, None
        await session.flush()
        await session.refresh(entry)
    return WaitlistOut.model_validate(entry)


@router.delete("/v1/waitlist/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def leave_waitlist(entry_id: UUID, user: User, session: Session) -> Response:
    async with transaction(session):
        entry = await session.get(WaitlistEntry, entry_id, with_for_update=True)
        if entry is None or entry.client_id != user.user_id or entry.status not in ("active", "notified"):
            raise BookingNotFound("Navbat yozuvi topilmadi", code="WAITLIST_NOT_FOUND")
        entry.status = "cancelled"
    return Response(status_code=status.HTTP_204_NO_CONTENT)
