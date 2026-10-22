"""Seans paketlari (C12): shifokor taklif qiladi, mijoz yoziladi; chegirma bron paytida quote'da qo'llanadi.
Oldindan to'lov yo'q, ishlatilmagan seanslar qaytarilmaydi; muddati o'tgan yozilish o'qishda yopiladi."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.db import transaction
from medbron_common.money import percent_of

from app.models import Package, PackageEnrollment
from app.repositories import pricing as repo
from app.services.errors import PackageAlreadyActive, PackageNotFound, ServiceNotOwned
from app.services.quotes import Deps, load_services


@dataclass(frozen=True)
class EnrollmentView:
    enrollment: PackageEnrollment
    sessions_left: int


async def offer_view(session: AsyncSession, deps: Deps, package: Package) -> dict[str, Any]:
    services = await load_services(session, deps, [package.service_id], package.doctor_id)
    service = services.get(package.service_id)
    unit = service.price_tiyin if service else 0
    return {
        "id": package.id,
        "doctor_id": package.doctor_id,
        "service_id": package.service_id,
        "service_name": service.name if service else "",
        "name": package.name,
        "sessions": package.sessions,
        "discount_percent": package.discount_percent,
        "validity_days": package.validity_days,
        "is_active": package.is_active,
        "unit_price_tiyin": unit,
        "total_saving_tiyin": percent_of(unit * package.sessions, package.discount_percent),
    }


async def _ensure_own_service(session: AsyncSession, deps: Deps, doctor_id: uuid.UUID, service_id: uuid.UUID) -> None:
    services = await load_services(session, deps, [service_id], doctor_id)
    service = services.get(service_id)
    if service is None or not service.is_active or (service.doctor_id is not None and service.doctor_id != doctor_id):
        raise ServiceNotOwned()


async def create_package(session: AsyncSession, deps: Deps, doctor_id: uuid.UUID, data: dict[str, Any]) -> Package:
    async with transaction(session):
        await _ensure_own_service(session, deps, doctor_id, data["service_id"])
        package = Package(id=uuid.uuid4(), doctor_id=doctor_id, **data)
        session.add(package)
    return package


async def update_package(
    session: AsyncSession, doctor_id: uuid.UUID, package_id: uuid.UUID, changes: dict[str, Any]
) -> Package:
    """Mavjud yozilishlar o'z nusxasini saqlaydi (seanslar, foiz) — o'zgarish faqat yangilariga ta'sir qiladi."""
    async with transaction(session):
        package = await repo.get_package(session, package_id)
        if package is None or package.doctor_id != doctor_id:
            raise PackageNotFound()
        for key, value in changes.items():
            setattr(package, key, value)
    return package


async def enroll(session: AsyncSession, deps: Deps, client_id: uuid.UUID, package_id: uuid.UUID) -> EnrollmentView:
    now = datetime.now(UTC)
    try:
        async with transaction(session):
            package = await repo.get_package(session, package_id)
            if package is None or not package.is_active:
                raise PackageNotFound()
            services = await load_services(session, deps, [package.service_id], package.doctor_id)
            service = services.get(package.service_id)
            if service is None or not service.is_active:
                raise PackageNotFound()
            await repo.expire_stale_enrollments(session, client_id, now)
            enrollment = PackageEnrollment(
                id=uuid.uuid4(),
                package_id=package.id,
                client_id=client_id,
                doctor_id=package.doctor_id,
                service_id=package.service_id,
                package_name=package.name,
                sessions_total=package.sessions,
                discount_percent=package.discount_percent,
                expires_at=now + timedelta(days=package.validity_days),
            )
            session.add(enrollment)
            await session.flush()
    except IntegrityError as exc:
        raise PackageAlreadyActive() from exc
    return EnrollmentView(enrollment, enrollment.sessions_total)


async def mine(session: AsyncSession, client_id: uuid.UUID) -> list[EnrollmentView]:
    now = datetime.now(UTC)
    async with transaction(session):
        await repo.expire_stale_enrollments(session, client_id, now)
    out = []
    for enrollment in await repo.active_enrollments(session, client_id, now=now):
        used = await repo.sessions_used(session, enrollment.id)
        out.append(EnrollmentView(enrollment, max(enrollment.sessions_total - used, 0)))
    return out
