"""search-service reindex uchun to'liq eksport (keyset sahifalash `id` bo'yicha). Faqat ommaviy ko'rinadigan
maydonlar: litsenziya, hujjatlar, moderatsiya izohi, to'lov sozlamalari CHIQMAYDI."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Affiliation, Clinic, Doctor, DoctorSpecialization, Service, Specialization

# Qidiruvda hech qachon bo'lmagan holatlar (draft, pending, rejected) eksport qilinmaydi
EXPORTED_DOCTOR_STATUSES = ("approved", "suspended")
EXPORTED_CLINIC_STATUSES = ("active", "suspended")


async def doctors_page(session: AsyncSession, after: uuid.UUID | None, limit: int) -> list[Doctor]:
    stmt = select(Doctor).where(Doctor.status.in_(EXPORTED_DOCTOR_STATUSES))
    if after is not None:
        stmt = stmt.where(Doctor.id > after)
    return list((await session.scalars(stmt.order_by(Doctor.id).limit(limit))).all())


async def clinics_page(session: AsyncSession, after: uuid.UUID | None, limit: int) -> list[Clinic]:
    stmt = select(Clinic).where(Clinic.status.in_(EXPORTED_CLINIC_STATUSES))
    if after is not None:
        stmt = stmt.where(Clinic.id > after)
    return list((await session.scalars(stmt.order_by(Clinic.id).limit(limit))).all())


async def specializations(session: AsyncSession) -> list[Specialization]:
    return list((await session.scalars(select(Specialization).order_by(Specialization.id))).all())


async def specialization_ids_for(
    session: AsyncSession, doctor_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    if doctor_ids:
        stmt = select(DoctorSpecialization.doctor_id, DoctorSpecialization.specialization_id).where(
            DoctorSpecialization.doctor_id.in_(doctor_ids)
        )
        for doctor_id, spec_id in (await session.execute(stmt)).all():
            out[doctor_id].append(spec_id)
    return out


async def affiliations_for(
    session: AsyncSession, doctor_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[Affiliation]]:
    out: dict[uuid.UUID, list[Affiliation]] = defaultdict(list)
    if doctor_ids:
        stmt = select(Affiliation).where(
            Affiliation.doctor_id.in_(doctor_ids), Affiliation.status.in_(("active", "paused"))
        )
        for a in (await session.scalars(stmt)).all():
            out[a.doctor_id].append(a)
    return out


async def services_for(
    session: AsyncSession, *, doctor_ids: Sequence[uuid.UUID] = (), clinic_ids: Sequence[uuid.UUID] = ()
) -> dict[uuid.UUID, list[Service]]:
    """Kalit — egasi (doctor_id yoki, klinika xizmati bo'lsa, clinic_id). Faqat faol xizmatlar."""
    out: dict[uuid.UUID, list[Service]] = defaultdict(list)
    if doctor_ids:
        stmt = select(Service).where(Service.doctor_id.in_(doctor_ids), Service.is_active.is_(True))
        for s in (await session.scalars(stmt.order_by(Service.id))).all():
            out[s.doctor_id].append(s)  # type: ignore[index]
    if clinic_ids:
        stmt = select(Service).where(
            Service.clinic_id.in_(clinic_ids), Service.doctor_id.is_(None), Service.is_active.is_(True)
        )
        for s in (await session.scalars(stmt.order_by(Service.id))).all():
            out[s.clinic_id].append(s)  # type: ignore[index]
    return out
