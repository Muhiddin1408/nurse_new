"""/v1/admin/bookings — admin JWT: ko'rish (`bookings.read`), boshqarish (`bookings.manage`)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from medbron_common.db import transaction

from app.core import permissions
from app.core.security import CurrentAdmin, require_permission
from app.repositories import bookings as repo
from app.routers.deps import DepsDep, Ip, Session, booking_out
from app.schemas.bookings import AdminCancelIn, AdminNoShowIn, BookingOut, NoteIn
from app.services import lifecycle
from app.services.errors import BookingNotFound

router = APIRouter(prefix="/v1/admin/bookings", tags=["admin"])
Reader = Annotated[CurrentAdmin, Depends(require_permission(permissions.BOOKINGS_READ))]
Manager = Annotated[CurrentAdmin, Depends(require_permission(permissions.BOOKINGS_MANAGE))]


@router.get("", response_model=list[BookingOut])
async def list_bookings(
    _: Reader,
    session: Session,
    status_: Annotated[str | None, Query(alias="status")] = None,
    doctor_id: UUID | None = None,
    client_id: UUID | None = None,
    number: str | None = None,
    start_from: datetime | None = None,
    start_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BookingOut]:
    rows = await repo.admin_list(
        session,
        status=status_,
        doctor_id=doctor_id,
        client_id=client_id,
        number=number,
        start_from=start_from,
        start_to=start_to,
        limit=limit,
        offset=offset,
    )
    items = await repo.items_for(session, [b.id for b in rows])
    return [booking_out(b, items[b.id]) for b in rows]


@router.get("/{booking_id}", response_model=BookingOut)
async def detail(booking_id: UUID, _: Reader, session: Session) -> BookingOut:
    b = await repo.get(session, booking_id)
    if b is None:
        raise BookingNotFound()
    return booking_out(b, await repo.items_of(session, b.id))


async def _locked(session: Session, booking_id: UUID):  # type: ignore[no-untyped-def]
    b = await repo.get(session, booking_id, for_update=True)
    if b is None:
        raise BookingNotFound()
    return b


@router.post("/{booking_id}/complete", response_model=BookingOut)
async def complete(booking_id: UUID, body: NoteIn, admin: Manager, session: Session) -> BookingOut:
    async with transaction(session):
        b = await _locked(session, booking_id)
        await lifecycle.complete(session, b, by="admin", note=body.note, actor_id=admin.admin_id)
    return booking_out(b, await repo.items_of(session, b.id))


@router.post("/{booking_id}/no-show", response_model=BookingOut)
async def no_show(booking_id: UUID, body: AdminNoShowIn, admin: Manager, session: Session) -> BookingOut:
    async with transaction(session):
        b = await _locked(session, booking_id)
        await lifecycle.mark_no_show(
            session, b, absent=body.absent, by="admin", note=body.note, actor_id=admin.admin_id
        )
    return booking_out(b, await repo.items_of(session, b.id))


@router.post("/{booking_id}/cancel", response_model=BookingOut)
async def cancel(
    booking_id: UUID, body: AdminCancelIn, admin: Manager, session: Session, deps: DepsDep, ip: Ip
) -> BookingOut:
    """Admin bekor qilishi — mijozga 100% (siyosatdan qat'i nazar)."""
    platform = await deps.settings.get()
    async with transaction(session):
        b = await _locked(session, booking_id)
        await lifecycle.cancel(
            session,
            b,
            actor="admin",
            reason_code="admin_cancelled",
            reason_text=body.reason,
            platform=platform,
            actor_id=admin.admin_id,
            ip=ip,
        )
    return booking_out(b, await repo.items_of(session, b.id))
