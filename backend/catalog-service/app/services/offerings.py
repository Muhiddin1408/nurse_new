"""Xizmatlar (D5): shifokorniki (tasdiqlangan shifokor) yoki klinikaniki (klinika admini, `doctor_id` NULL).

- Narx tiyinda, 0 < narx ≤ 10 mlrd tiyin (100 mln so'm); davomiylik 5–480 daq.
- Narx 50% dan ko'p oshsa — `confirm_large_change` talab qilinadi (409); har o'zgarish jurnalga va auditga.
- O'chirish — har doim `is_active=false` (catalog qaysi xizmat bron qilinganini bilmaydi; bron narx nusxasini saqlaydi).
- Fiskal kodlar (MXIK) API orqali yozilmaydi — admin/ko'chirish (B12).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.events import Events

from app.events import outbox
from app.models import Doctor, Service, ServicePriceHistory
from app.repositories import catalog as repo
from app.services.clinics import require_managed
from app.services.common import service_payload
from app.services.doctors import require_approved
from app.services.errors import InvalidRequest, LargePriceChange, ServiceNotFound, SpecializationNotFound

LARGE_CHANGE_RATIO_NUM, LARGE_CHANGE_RATIO_DEN = 3, 2  # > 1.5×
EDITABLE = ("name", "name_ru", "description", "place", "duration_minutes", "price_tiyin", "specialization_id")


async def _validate(session: AsyncSession, data: dict[str, Any], *, doctor: Doctor | None, clinic_owned: bool) -> None:
    if "specialization_id" in data:
        if await repo.count_existing_specializations(session, [data["specialization_id"]]) != 1:
            raise SpecializationNotFound()
        if doctor is not None and data["specialization_id"] not in await repo.specialization_ids(session, doctor.id):
            raise InvalidRequest("Xizmat mutaxassisligi shifokor mutaxassisliklaridan biri bo'lishi kerak")
    if data.get("place") == "home":
        if clinic_owned:
            raise InvalidRequest("Klinika xizmati uy chaqiruvi bo'la olmaydi")
        if doctor is not None and not doctor.accepts_home_visits:
            raise InvalidRequest("Uy chaqiruvi xizmati uchun profilda uy chaqiruvini yoqing")


def _emit(session: AsyncSession, event: str, service: Service, **extra: Any) -> None:
    outbox.add_event(session, event, aggregate_id=service.id, data={**service_payload(service), **extra})


async def create(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    user_id: uuid.UUID,
    clinic_id: uuid.UUID | None = None,
    ip: str | None,
) -> Service:
    doctor = None
    if clinic_id is None:
        doctor = await require_approved(session, user_id)
    else:
        await require_managed(session, user_id, clinic_id)
    await _validate(session, data, doctor=doctor, clinic_owned=clinic_id is not None)
    service = Service(
        id=uuid.uuid4(), doctor_id=doctor.id if doctor else None, clinic_id=clinic_id, is_active=True, **data
    )
    session.add(service)
    await session.flush()
    _emit(session, Events.SERVICE_CREATED, service)
    role = "doctor" if doctor else "clinic_admin"
    record_audit(
        outbox,
        session,
        Actor("user", user_id, role),
        action="service.create",
        object_type="service",
        object_id=service.id,
        after={"name": service.name, "price_tiyin": service.price_tiyin},
        ip=ip,
    )
    return service


async def _owned(
    session: AsyncSession, service_id: uuid.UUID, *, user_id: uuid.UUID, clinic_id: uuid.UUID | None
) -> tuple[Service, Doctor | None]:
    doctor = None
    if clinic_id is None:
        doctor = await require_approved(session, user_id)
    else:
        await require_managed(session, user_id, clinic_id)
    service = await repo.get_service(session, service_id, for_update=True)
    if service is None:
        raise ServiceNotFound()
    owned = (
        service.doctor_id == doctor.id
        if doctor is not None
        else service.clinic_id == clinic_id and service.doctor_id is None
    )
    if not owned:
        raise ServiceNotFound()
    return service, doctor


async def update(
    session: AsyncSession,
    service_id: uuid.UUID,
    changes: dict[str, Any],
    *,
    user_id: uuid.UUID,
    clinic_id: uuid.UUID | None = None,
    confirm_large_change: bool = False,
    ip: str | None,
) -> Service:
    service, doctor = await _owned(session, service_id, user_id=user_id, clinic_id=clinic_id)
    await _validate(session, changes, doctor=doctor, clinic_owned=clinic_id is not None)
    previous = service.price_tiyin
    new_price = changes.get("price_tiyin", previous)
    large = new_price * LARGE_CHANGE_RATIO_DEN > previous * LARGE_CHANGE_RATIO_NUM
    if large and not confirm_large_change:
        raise LargePriceChange(extra={"old_price_tiyin": previous, "new_price_tiyin": new_price})
    activate = changes.pop("is_active", None)
    for key in EDITABLE:
        if key in changes:
            setattr(service, key, changes[key])
    if activate is True:
        service.is_active = True
    if new_price != previous:
        session.add(
            ServicePriceHistory(
                service_id=service.id,
                old_price_tiyin=previous,
                new_price_tiyin=new_price,
                changed_by=user_id,
                is_large_change=large,
            )
        )
    await session.flush()
    _emit(session, Events.SERVICE_UPDATED, service, previous_price_tiyin=previous)
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "doctor" if doctor else "clinic_admin"),
        action="service.update",
        object_type="service",
        object_id=service.id,
        before={"price_tiyin": previous},
        after={k: str(v) for k, v in changes.items()},
        ip=ip,
    )
    return service


async def deactivate(
    session: AsyncSession,
    service_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    clinic_id: uuid.UUID | None = None,
    ip: str | None,
) -> Service:
    service, doctor = await _owned(session, service_id, user_id=user_id, clinic_id=clinic_id)
    if not service.is_active:
        return service
    service.is_active = False
    outbox.add_event(
        session,
        Events.SERVICE_DEACTIVATED,
        aggregate_id=service.id,
        data={"service_id": service.id, "doctor_id": service.doctor_id, "clinic_id": service.clinic_id},
    )
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "doctor" if doctor else "clinic_admin"),
        action="service.deactivate",
        object_type="service",
        object_id=service.id,
        ip=ip,
    )
    return service


async def doctor_services(session: AsyncSession, user_id: uuid.UUID) -> Sequence[Service]:
    doctor = await require_approved(session, user_id)
    stmt = select(Service).where(Service.doctor_id == doctor.id).order_by(Service.created_at)
    return (await session.scalars(stmt)).all()


async def clinic_services(session: AsyncSession, user_id: uuid.UUID, clinic_id: uuid.UUID) -> Sequence[Service]:
    await require_managed(session, user_id, clinic_id)
    stmt = (
        select(Service).where(Service.clinic_id == clinic_id, Service.doctor_id.is_(None)).order_by(Service.created_at)
    )
    return (await session.scalars(stmt)).all()


async def price_history(
    session: AsyncSession, user_id: uuid.UUID, service_id: uuid.UUID
) -> Sequence[ServicePriceHistory]:
    await _owned(session, service_id, user_id=user_id, clinic_id=None)
    stmt = (
        select(ServicePriceHistory)
        .where(ServicePriceHistory.service_id == service_id)
        .order_by(ServicePriceHistory.created_at.desc())
    )
    return (await session.scalars(stmt)).all()
