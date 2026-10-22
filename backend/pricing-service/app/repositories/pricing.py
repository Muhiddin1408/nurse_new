"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ACTIVE,
    RELEASED,
    RESERVED,
    ClientBooking,
    Package,
    PackageEnrollment,
    PackageUse,
    PromoCode,
    PromoRedemption,
    ServiceCache,
)

TERMINAL_BOOKING = ("cancelled", "expired", "completed", "no_show")
PREVIOUS_BOOKING = ("confirmed", "completed", "no_show")
LIVE = (RESERVED, ACTIVE)

# --- services cache --------------------------------------------------------------------------


async def services_by_ids(session: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ServiceCache]:
    if not ids:
        return {}
    rows = (await session.scalars(select(ServiceCache).where(ServiceCache.service_id.in_(ids)))).all()
    return {r.service_id: r for r in rows}


async def upsert_service(session: AsyncSession, values: dict[str, Any], *, keep_fiscal: bool) -> None:
    """Yangiroq `changed_at` qo'llanadi. Eventda fiskal kodlar yo'q — `keep_fiscal` bo'lsa ular saqlanadi."""
    stmt = insert(ServiceCache).values(**values)
    update_set = {k: v for k, v in values.items() if k != "service_id"}
    if keep_fiscal:
        for key in ("mxik_code", "package_code", "vat_percent"):
            update_set.pop(key, None)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[ServiceCache.service_id],
            set_=update_set,
            where=ServiceCache.changed_at <= values["changed_at"],
        )
    )


async def deactivate_service(session: AsyncSession, service_id: uuid.UUID, changed_at: datetime) -> None:
    await session.execute(
        update(ServiceCache)
        .where(ServiceCache.service_id == service_id, ServiceCache.changed_at <= changed_at)
        .values(is_active=False, changed_at=changed_at)
    )


# --- client stats ----------------------------------------------------------------------------


async def apply_booking(session: AsyncSession, values: dict[str, Any]) -> None:
    """Yangiroq event qo'llanadi; yakuniy holat qaytarilmaydi."""
    stmt = insert(ClientBooking).values(**values)
    update_set = {k: v for k, v in values.items() if k != "booking_id"}
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[ClientBooking.booking_id],
            set_=update_set,
            where=and_(ClientBooking.changed_at < values["changed_at"], ClientBooking.status.not_in(TERMINAL_BOOKING)),
        )
    )


async def has_previous_booking(session: AsyncSession, client_id: uuid.UUID) -> bool:
    stmt = select(ClientBooking.booking_id).where(
        ClientBooking.client_id == client_id, ClientBooking.status.in_(PREVIOUS_BOOKING)
    )
    return (await session.execute(stmt.limit(1))).first() is not None


async def has_recent_completed(
    session: AsyncSession, client_id: uuid.UUID, doctor_id: uuid.UUID, since: datetime
) -> bool:
    stmt = select(ClientBooking.booking_id).where(
        ClientBooking.client_id == client_id,
        ClientBooking.doctor_id == doctor_id,
        ClientBooking.status == "completed",
        ClientBooking.completed_at >= since,
    )
    return (await session.execute(stmt.limit(1))).first() is not None


async def client_no_shows(session: AsyncSession, client_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(
        ClientBooking.client_id == client_id, ClientBooking.status == "no_show", ClientBooking.no_show_by == "client"
    )
    return int((await session.execute(stmt)).scalar_one())


async def forget_client(session: AsyncSession, client_id: uuid.UUID) -> None:
    await session.execute(delete(ClientBooking).where(ClientBooking.client_id == client_id))
    await session.execute(
        update(PackageEnrollment).where(PackageEnrollment.client_id == client_id).values(is_active=False)
    )
    await session.execute(update(PromoRedemption).where(PromoRedemption.client_id == client_id).values(client_id=None))


# --- promo -----------------------------------------------------------------------------------


async def promo_by_code(session: AsyncSession, code: str, *, for_update: bool = False) -> PromoCode | None:
    stmt = select(PromoCode).where(PromoCode.code == code, PromoCode.is_active.is_(True))
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def promo_usage(session: AsyncSession, promo_id: uuid.UUID, client_id: uuid.UUID) -> tuple[int, int]:
    stmt = select(
        func.count(),
        func.count().filter(PromoRedemption.client_id == client_id),
    ).where(PromoRedemption.promo_id == promo_id, PromoRedemption.state.in_(LIVE))
    total, mine = (await session.execute(stmt)).one()
    return int(total), int(mine)


async def redemption_for_booking(session: AsyncSession, booking_id: uuid.UUID) -> PromoRedemption | None:
    stmt = select(PromoRedemption).where(PromoRedemption.booking_id == booking_id)
    return (await session.scalars(stmt)).one_or_none()


async def list_promos(session: AsyncSession, *, limit: int, offset: int) -> Sequence[PromoCode]:
    stmt = select(PromoCode).order_by(PromoCode.created_at.desc()).limit(limit).offset(offset)
    return (await session.scalars(stmt)).all()


async def promo_live_uses(session: AsyncSession, promo_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not promo_ids:
        return {}
    stmt = (
        select(PromoRedemption.promo_id, func.count())
        .where(PromoRedemption.promo_id.in_(promo_ids), PromoRedemption.state.in_(LIVE))
        .group_by(PromoRedemption.promo_id)
    )
    return {row[0]: int(row[1]) for row in (await session.execute(stmt)).all()}


# --- packages --------------------------------------------------------------------------------


async def sessions_used(session: AsyncSession, enrollment_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(PackageUse.enrollment_id == enrollment_id, PackageUse.state.in_(LIVE))
    return int((await session.execute(stmt)).scalar_one())


async def expire_stale_enrollments(session: AsyncSession, client_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(PackageEnrollment)
        .where(
            PackageEnrollment.client_id == client_id,
            PackageEnrollment.is_active.is_(True),
            PackageEnrollment.expires_at <= now,
        )
        .values(is_active=False)
    )


async def active_enrollments(
    session: AsyncSession,
    client_id: uuid.UUID,
    *,
    now: datetime,
    doctor_id: uuid.UUID | None = None,
    service_ids: Sequence[uuid.UUID] | None = None,
) -> Sequence[PackageEnrollment]:
    stmt = select(PackageEnrollment).where(
        PackageEnrollment.client_id == client_id,
        PackageEnrollment.is_active.is_(True),
        PackageEnrollment.expires_at > now,
    )
    if doctor_id is not None:
        stmt = stmt.where(PackageEnrollment.doctor_id == doctor_id)
    if service_ids is not None:
        stmt = stmt.where(PackageEnrollment.service_id.in_(service_ids))
    return (await session.scalars(stmt.order_by(PackageEnrollment.expires_at))).all()


async def get_enrollment(
    session: AsyncSession, enrollment_id: uuid.UUID, *, for_update: bool = False
) -> PackageEnrollment | None:
    stmt = select(PackageEnrollment).where(PackageEnrollment.id == enrollment_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def has_other_active_enrollment(session: AsyncSession, enrollment: PackageEnrollment) -> bool:
    stmt = select(PackageEnrollment.id).where(
        PackageEnrollment.client_id == enrollment.client_id,
        PackageEnrollment.service_id == enrollment.service_id,
        PackageEnrollment.is_active.is_(True),
        PackageEnrollment.id != enrollment.id,
    )
    return (await session.execute(stmt.limit(1))).first() is not None


async def use_for_booking(session: AsyncSession, booking_id: uuid.UUID) -> PackageUse | None:
    return (await session.scalars(select(PackageUse).where(PackageUse.booking_id == booking_id))).one_or_none()


async def get_package(session: AsyncSession, package_id: uuid.UUID) -> Package | None:
    return await session.get(Package, package_id)


async def list_packages(session: AsyncSession, *, doctor_id: uuid.UUID, only_active: bool) -> Sequence[Package]:
    stmt = select(Package).where(Package.doctor_id == doctor_id)
    if only_active:
        stmt = stmt.where(Package.is_active.is_(True))
    return (await session.scalars(stmt.order_by(Package.created_at))).all()


# --- band qilishlar (reserved → active → released) -------------------------------------------


async def activate_reservations(session: AsyncSession, booking_id: uuid.UUID) -> None:
    for model in (PromoRedemption, PackageUse):
        await session.execute(
            update(model).where(model.booking_id == booking_id, model.state == RESERVED).values(state=ACTIVE)
        )


async def release_for_booking(session: AsyncSession, booking_id: uuid.UUID) -> PackageUse | None:
    """Promo va paket band qilishini bo'shatadi.
    Qaytaradi: bo'shatilgan paket ishlatilishi (yozilishni qayta faollashtirish uchun)."""
    await session.execute(
        update(PromoRedemption)
        .where(PromoRedemption.booking_id == booking_id, PromoRedemption.state.in_(LIVE))
        .values(state=RELEASED)
    )
    stmt = (
        update(PackageUse)
        .where(PackageUse.booking_id == booking_id, PackageUse.state.in_(LIVE))
        .values(state=RELEASED)
        .returning(PackageUse)
    )
    return (await session.scalars(stmt)).one_or_none()


async def stale_reservations(
    session: AsyncSession, before: datetime, limit: int
) -> tuple[Sequence[PromoRedemption], Sequence[PackageUse]]:
    promos = (
        await session.scalars(
            select(PromoRedemption)
            .where(PromoRedemption.state == RESERVED, PromoRedemption.reserved_at < before)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    uses = (
        await session.scalars(
            select(PackageUse)
            .where(PackageUse.state == RESERVED, PackageUse.reserved_at < before)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    return promos, uses
