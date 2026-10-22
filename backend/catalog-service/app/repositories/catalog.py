"""Ma'lumotlar bazasi so'rovlari (qatlam qoidasi: routers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Affiliation,
    Clinic,
    ClinicMembership,
    Doctor,
    DoctorDocument,
    DoctorSpecialization,
    Room,
    Service,
    Specialization,
)

# --- doctors ---------------------------------------------------------------------------------


async def doctor_by_user(session: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False) -> Doctor | None:
    stmt = select(Doctor).where(Doctor.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def get_doctor(session: AsyncSession, doctor_id: uuid.UUID, *, for_update: bool = False) -> Doctor | None:
    stmt = select(Doctor).where(Doctor.id == doctor_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def specialization_ids(session: AsyncSession, doctor_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(DoctorSpecialization.specialization_id).where(DoctorSpecialization.doctor_id == doctor_id)
    return sorted((await session.scalars(stmt)).all(), key=str)


async def specializations_by_doctor(
    session: AsyncSession, doctor_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[Specialization]]:
    if not doctor_ids:
        return {}
    stmt = (
        select(DoctorSpecialization.doctor_id, Specialization)
        .join(Specialization, Specialization.id == DoctorSpecialization.specialization_id)
        .where(DoctorSpecialization.doctor_id.in_(doctor_ids))
        .order_by(Specialization.name)
    )
    out: dict[uuid.UUID, list[Specialization]] = {}
    for doctor_id, spec in (await session.execute(stmt)).all():
        out.setdefault(doctor_id, []).append(spec)
    return out


async def set_specializations(session: AsyncSession, doctor_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
    await session.execute(delete(DoctorSpecialization).where(DoctorSpecialization.doctor_id == doctor_id))
    for sid in dict.fromkeys(ids):
        session.add(DoctorSpecialization(doctor_id=doctor_id, specialization_id=sid))
    await session.flush()


async def documents(session: AsyncSession, doctor_id: uuid.UUID) -> Sequence[DoctorDocument]:
    stmt = select(DoctorDocument).where(DoctorDocument.doctor_id == doctor_id).order_by(DoctorDocument.created_at)
    return (await session.scalars(stmt)).all()


# --- clinics / affiliations ------------------------------------------------------------------


async def get_clinic(session: AsyncSession, clinic_id: uuid.UUID, *, for_update: bool = False) -> Clinic | None:
    stmt = select(Clinic).where(Clinic.id == clinic_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def managed_clinic_ids(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """User faol a'zo bo'lgan klinikalar (klinika holatidan qat'i nazar — to'xtatilgan klinikani ham ko'radi)."""
    stmt = select(ClinicMembership.clinic_id).where(
        ClinicMembership.user_id == user_id, ClinicMembership.is_active.is_(True)
    )
    return sorted((await session.scalars(stmt)).all(), key=str)


async def get_membership(session: AsyncSession, user_id: uuid.UUID, clinic_id: uuid.UUID) -> ClinicMembership | None:
    stmt = select(ClinicMembership).where(ClinicMembership.user_id == user_id, ClinicMembership.clinic_id == clinic_id)
    return (await session.scalars(stmt.with_for_update())).one_or_none()


async def get_affiliation(
    session: AsyncSession, affiliation_id: uuid.UUID, *, for_update: bool = False
) -> Affiliation | None:
    stmt = select(Affiliation).where(Affiliation.id == affiliation_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def affiliation_pair(session: AsyncSession, doctor_id: uuid.UUID, clinic_id: uuid.UUID) -> Affiliation | None:
    stmt = select(Affiliation).where(Affiliation.doctor_id == doctor_id, Affiliation.clinic_id == clinic_id)
    return (await session.scalars(stmt.with_for_update())).one_or_none()


async def active_clinic_ids(session: AsyncSession, doctor_id: uuid.UUID) -> list[uuid.UUID]:
    """Faol hamkorlik VA faol klinika — shifokor shu klinikada bron qabul qiladi."""
    stmt = (
        select(Affiliation.clinic_id)
        .join(Clinic, Clinic.id == Affiliation.clinic_id)
        .where(Affiliation.doctor_id == doctor_id, Affiliation.status == "active", Clinic.status == "active")
    )
    return sorted((await session.scalars(stmt)).all(), key=str)


async def active_affiliated_doctor_ids(session: AsyncSession, clinic_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(Affiliation.doctor_id).where(Affiliation.clinic_id == clinic_id, Affiliation.status == "active")
    return list((await session.scalars(stmt)).all())


async def get_room(session: AsyncSession, room_id: uuid.UUID) -> Room | None:
    return await session.get(Room, room_id)


# --- services --------------------------------------------------------------------------------


def bookable_services_query(doctor_id: uuid.UUID) -> Select[Service]:
    """A3: faol VA (shifokorning o'zi YOKI shifokor faol hamkorlikdagi faol klinikaning klinika-xizmati)."""
    active_clinics = (
        select(Affiliation.clinic_id)
        .join(Clinic, Clinic.id == Affiliation.clinic_id)
        .where(Affiliation.doctor_id == doctor_id, Affiliation.status == "active", Clinic.status == "active")
    )
    return select(Service).where(
        Service.is_active.is_(True),
        or_(
            Service.doctor_id == doctor_id,
            and_(Service.doctor_id.is_(None), Service.clinic_id.in_(active_clinics)),
        ),
    )


async def get_service(session: AsyncSession, service_id: uuid.UUID, *, for_update: bool = False) -> Service | None:
    stmt = select(Service).where(Service.id == service_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def specializations_map(session: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Specialization]:
    if not ids:
        return {}
    rows = (await session.scalars(select(Specialization).where(Specialization.id.in_(set(ids))))).all()
    return {s.id: s for s in rows}


async def count_existing_specializations(session: AsyncSession, ids: Sequence[uuid.UUID]) -> int:
    if not ids:
        return 0
    stmt = select(func.count()).where(Specialization.id.in_(set(ids)))
    return int((await session.execute(stmt)).scalar_one())
