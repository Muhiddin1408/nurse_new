"""Proyeksiya so'rovlari (qatlam qoidasi: routers/workers → services → repositories → models)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select, true, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AffiliationRow, ClinicRow, DoctorRow, ReindexRun, ServiceRow, SpecializationRow


def _locked(stmt: Any) -> Any:
    """FOR UPDATE + identity map'dagi obyektni qulf ostidagi qiymatlar bilan yangilash."""
    return stmt.with_for_update().execution_options(populate_existing=True)


async def lock_doctor(session: AsyncSession, doctor_id: uuid.UUID) -> DoctorRow:
    """Bo'lmasa — `unknown` holatli bo'sh qator (event approved'dan oldin kelishi mumkin), keyin qulf."""
    await session.flush()  # populate_existing flush qilinmagan o'zgarishlarni yo'qotmasin
    await session.execute(insert(DoctorRow).values(id=doctor_id).on_conflict_do_nothing())
    row: DoctorRow = (await session.scalars(_locked(select(DoctorRow).where(DoctorRow.id == doctor_id)))).one()
    return row


async def lock_clinic(session: AsyncSession, clinic_id: uuid.UUID) -> ClinicRow:
    await session.flush()
    await session.execute(insert(ClinicRow).values(id=clinic_id).on_conflict_do_nothing())
    row: ClinicRow = (await session.scalars(_locked(select(ClinicRow).where(ClinicRow.id == clinic_id)))).one()
    return row


async def lock_doctors(session: AsyncSession, ids: Sequence[uuid.UUID]) -> list[DoctorRow]:
    """Tartiblangan qulf (deadlock'siz): klinika o'zgarishi ko'p shifokorni qayta quradi."""
    if not ids:
        return []
    await session.flush()
    stmt = _locked(select(DoctorRow).where(DoctorRow.id.in_(ids)).order_by(DoctorRow.id))
    return list((await session.scalars(stmt)).all())


async def lock_clinics(session: AsyncSession, ids: Sequence[uuid.UUID]) -> list[ClinicRow]:
    if not ids:
        return []
    await session.flush()
    stmt = _locked(select(ClinicRow).where(ClinicRow.id.in_(ids)).order_by(ClinicRow.id))
    return list((await session.scalars(stmt)).all())


async def doctors_by_ids(session: AsyncSession, ids: Sequence[uuid.UUID]) -> list[DoctorRow]:
    if not ids:
        return []
    return list((await session.scalars(select(DoctorRow).where(DoctorRow.id.in_(ids)).order_by(DoctorRow.id))).all())


async def get_affiliation(session: AsyncSession, affiliation_id: uuid.UUID) -> AffiliationRow | None:
    await session.flush()
    stmt = _locked(select(AffiliationRow).where(AffiliationRow.id == affiliation_id))
    return (await session.scalars(stmt)).one_or_none()


async def get_service(session: AsyncSession, service_id: uuid.UUID) -> ServiceRow | None:
    await session.flush()
    return (await session.scalars(_locked(select(ServiceRow).where(ServiceRow.id == service_id)))).one_or_none()


async def affiliations_of_doctor(session: AsyncSession, doctor_id: uuid.UUID) -> list[AffiliationRow]:
    stmt = select(AffiliationRow).where(AffiliationRow.doctor_id == doctor_id)
    return list((await session.scalars(stmt)).all())


async def active_doctor_ids_of_clinic(session: AsyncSession, clinic_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(AffiliationRow.doctor_id).where(
        AffiliationRow.clinic_id == clinic_id, AffiliationRow.status.in_(("active", "paused"))
    )
    return sorted(set((await session.scalars(stmt)).all()))


async def doctor_ids_of_clinic(session: AsyncSession, clinic_id: uuid.UUID) -> list[uuid.UUID]:
    """Har qanday holatdagi hamkorlik — klinika o'zgarsa hujjatlari qayta quriladigan shifokorlar."""
    stmt = select(AffiliationRow.doctor_id).where(AffiliationRow.clinic_id == clinic_id)
    return sorted(set((await session.scalars(stmt)).all()))


async def clinics_by_ids(session: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ClinicRow]:
    if not ids:
        return {}
    rows = await session.scalars(select(ClinicRow).where(ClinicRow.id.in_(ids)))
    return {c.id: c for c in rows.all()}


async def active_services(
    session: AsyncSession, *, doctor_ids: Sequence[uuid.UUID] = (), clinic_ids: Sequence[uuid.UUID] = ()
) -> list[ServiceRow]:
    """Faol xizmatlar: shifokorning o'zi va (berilgan) klinikalarning o'z xizmatlari."""
    conds: list[Any] = []
    if doctor_ids:
        conds.append(ServiceRow.doctor_id.in_(doctor_ids))
    if clinic_ids:
        conds.append((ServiceRow.clinic_id.in_(clinic_ids)) & (ServiceRow.doctor_id.is_(None)))
    if not conds:
        return []
    stmt = select(ServiceRow).where(ServiceRow.is_active.is_(True), or_(*conds)).order_by(ServiceRow.id)
    return list((await session.scalars(stmt)).all())


async def specializations_by_ids(session: AsyncSession, ids: Sequence[str]) -> list[SpecializationRow]:
    if not ids:
        return []
    uuids = [uuid.UUID(str(i)) for i in ids]
    rows = await session.scalars(select(SpecializationRow).where(SpecializationRow.id.in_(uuids)))
    by_id = {r.id: r for r in rows.all()}
    return [by_id[u] for u in uuids if u in by_id]


async def upsert_specializations(session: AsyncSession, items: list[dict[str, Any]]) -> int:
    if not items:
        return 0
    stmt = insert(SpecializationRow).values(
        [
            {"id": uuid.UUID(i["id"]), "name": i["name"], "name_ru": i.get("name_ru", ""), "slug": i["slug"]}
            for i in items
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SpecializationRow.id],
        set_={"name": stmt.excluded.name, "name_ru": stmt.excluded.name_ru, "slug": stmt.excluded.slug},
    )
    await session.execute(stmt)
    return len(items)


async def suggest_specializations(session: AsyncSession, prefix: str, limit: int) -> list[SpecializationRow]:
    pattern = prefix.replace("%", r"\%").replace("_", r"\_") + "%"
    stmt = (
        select(SpecializationRow)
        .where(or_(SpecializationRow.name.ilike(pattern), SpecializationRow.name_ru.ilike(pattern)))
        .order_by(SpecializationRow.name)
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def all_doctor_ids(session: AsyncSession) -> list[uuid.UUID]:
    return list((await session.scalars(select(DoctorRow.id).order_by(DoctorRow.id))).all())


async def all_clinic_ids(session: AsyncSession) -> list[uuid.UUID]:
    return list((await session.scalars(select(ClinicRow.id).order_by(ClinicRow.id))).all())


async def mark_missing_doctors(session: AsyncSession, seen: set[uuid.UUID], snapshot_at: datetime) -> list[uuid.UUID]:
    """Snapshot'da yo'q (rad etilgan/o'chirilgan) va snapshot'dan keyin event olmagan shifokorlar → removed."""
    stmt = (
        update(DoctorRow)
        .where(DoctorRow.id.not_in(seen) if seen else true(), DoctorRow.status_at < snapshot_at)
        .where(DoctorRow.status != "removed")
        .values(status="removed", status_at=snapshot_at)
        .returning(DoctorRow.id)
    )
    return list((await session.scalars(stmt)).all())


async def mark_missing_clinics(session: AsyncSession, seen: set[uuid.UUID], snapshot_at: datetime) -> list[uuid.UUID]:
    stmt = (
        update(ClinicRow)
        .where(ClinicRow.id.not_in(seen) if seen else true(), ClinicRow.status_at < snapshot_at)
        .where(ClinicRow.status != "removed")
        .values(status="removed", status_at=snapshot_at)
        .returning(ClinicRow.id)
    )
    return list((await session.scalars(stmt)).all())


# --------------------------------------------------------------------------- reindex


async def active_run(session: AsyncSession) -> ReindexRun | None:
    stmt = select(ReindexRun).where(ReindexRun.status.in_(("pending", "running"))).order_by(ReindexRun.created_at)
    return (await session.scalars(stmt)).first()


async def running_targets(session: AsyncSession) -> ReindexRun | None:
    """Ikki tomonlama yozish uchun: reindex yangi indeksni to'ldirayotgan bo'lsa."""
    stmt = select(ReindexRun).where(ReindexRun.status == "running", ReindexRun.doctors_index != "")
    return (await session.scalars(stmt)).first()


async def get_run(session: AsyncSession, run_id: uuid.UUID, *, for_update: bool = False) -> ReindexRun | None:
    stmt = select(ReindexRun).where(ReindexRun.id == run_id)
    if for_update:
        await session.flush()
        stmt = _locked(stmt)
    return (await session.scalars(stmt)).one_or_none()


async def recent_runs(session: AsyncSession, limit: int) -> list[ReindexRun]:
    stmt = select(ReindexRun).order_by(ReindexRun.created_at.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())


async def next_pending(session: AsyncSession) -> ReindexRun | None:
    await session.flush()
    stmt = (
        select(ReindexRun)
        .where(ReindexRun.status == "pending")
        .order_by(ReindexRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return (await session.scalars(stmt)).first()
