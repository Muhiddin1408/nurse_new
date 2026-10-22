"""Qayta indekslash (2026-10-21 qarori: manba — catalog `/internal/v1/export/*`).

    POST /v1/admin/search/reindex  → `reindex_runs(pending)` (bir vaqtda bitta; audit izi — kim/qachon/natija)
    scheduler (har 5 s)            → `execute()`:
        1. yangi `doctors_v<N+1>`, `clinics_v<N+1>` (mapping kodi bilan); run `running` — shu paytdan eventlar
           IKKALA indeksga yoziladi (documents.publish), hech narsa yo'qolmaydi
        2. eksport → proyeksiya: faqat snapshot'dan ESKI jihatlar yangilanadi (reindex paytida kelgan event yutadi)
        3. snapshot'da yo'q shifokor/klinika — `removed` (qidiruvdan chiqadi)
        4. barcha hujjatlar yangi indeksga → refresh → alias atomik almashtiriladi → eski indeks o'chiriladi
    Xatoda: run `failed`, yangi indekslar o'chiriladi, alias eski indeksda qoladi (qidiruv uzilmaydi).

`next_available_at` eksportda yo'q (schedule'niki) — proyeksiyadagi qiymat saqlanadi.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medbron_common.db import transaction
from medbron_common.errors import Conflict
from medbron_common.eventbus.envelope import new_event_id

from app.clients import elastic
from app.clients.catalog import CatalogClient
from app.core.config import get_settings
from app.metrics import REINDEX_RUNS
from app.models import AffiliationRow, DoctorRow, ReindexRun, ServiceRow
from app.repositories import projection as repo
from app.services import documents

logger = logging.getLogger(__name__)

BATCH = 200
VERSION = re.compile(r"_v(\d+)$")


class ReindexRunning(Conflict):
    code = "REINDEX_RUNNING"
    message = "Qayta indekslash allaqachon navbatda yoki bajarilmoqda"


async def request(session: AsyncSession, *, admin_id: uuid.UUID, ip: str | None) -> ReindexRun:
    stale_before = datetime.now(UTC) - timedelta(minutes=get_settings().reindex_stale_minutes)
    async with transaction(session):
        active = await repo.active_run(session)
        if (
            active is not None
            and active.status == "running"
            and (active.started_at or active.created_at) < stale_before
        ):
            # Yiqilgan jarayon (konteyner o'ldi) — yangisiga yo'l ochiladi
            active.status, active.error, active.finished_at = "failed", "stale (timeout)", datetime.now(UTC)
            await session.flush()
            active = None
        if active is not None:
            raise ReindexRunning()
        run = ReindexRun(id=new_event_id(), status="pending", requested_by=admin_id, requested_ip=ip)
        session.add(run)
        try:
            await session.flush()
        except IntegrityError:
            raise ReindexRunning() from None
    logger.info("reindex requested", extra={"run_id": str(run.id), "admin_id": str(admin_id), "ip": ip})
    return run


def _next_version(current: str | None) -> int:
    match = VERSION.search(current or "")
    return int(match.group(1)) + 1 if match else 2


def _ts(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


async def _apply_doctor(session: AsyncSession, item: dict[str, Any], snap: datetime) -> uuid.UUID:
    d: DoctorRow = await repo.lock_doctor(session, uuid.UUID(item["doctor_id"]))
    if d.profile_at < snap:
        d.user_id = uuid.UUID(item["user_id"])
        d.full_name = item["full_name"]
        d.specialization_ids = [str(x) for x in item["specialization_ids"]]
        d.experience_years = int(item["experience_years"])
        d.languages = list(item["languages"])
        d.accepts_home_visits = bool(item["accepts_home_visits"])
        d.home_visit_radius_km = int(item["home_visit_radius_km"])
        base = item.get("home_base")
        d.home_lat, d.home_lng = (float(base["lat"]), float(base["lng"])) if base else (None, None)
        d.photo_file_id = uuid.UUID(item["photo_file_id"]) if item.get("photo_file_id") else None
        d.profile_at = snap
    if d.status_at < snap:
        d.status, d.status_at = item["status"], snap
    if d.rating_at < snap:
        d.rating, d.reviews_count, d.rating_at = item["rating"], int(item["reviews_count"]), snap
    # Hamkorliklar: eksportdagilar (active/paused) — yangilanadi; proyeksiyada bor, eksportda yo'q — tugagan
    exported = {uuid.UUID(a["affiliation_id"]): a for a in item["affiliations"]}
    for row in await repo.affiliations_of_doctor(session, d.id):
        if row.at < snap:
            row.status, row.at = (exported[row.id]["status"] if row.id in exported else "ended"), snap
        exported.pop(row.id, None)
    for aid, a in exported.items():
        session.add(
            AffiliationRow(id=aid, doctor_id=d.id, clinic_id=uuid.UUID(a["clinic_id"]), status=a["status"], at=snap)
        )
        await repo.lock_clinic(session, uuid.UUID(a["clinic_id"]))
    await _apply_services(session, item["services"], snap, doctor_id=d.id)
    return d.id


async def _apply_services(
    session: AsyncSession, items: list[dict[str, Any]], snap: datetime, *, doctor_id: uuid.UUID | None = None,
    clinic_id: uuid.UUID | None = None,
) -> None:  # fmt: skip
    """Eksport faqat FAOL xizmatlarni beradi: egasining eksportda yo'q xizmatlari nofaol deb belgilanadi."""
    seen: set[uuid.UUID] = set()
    for s in items:
        sid = uuid.UUID(s["id"])
        seen.add(sid)
        row = await repo.get_service(session, sid)
        if row is None:
            row = ServiceRow(id=sid, at=datetime.fromtimestamp(0, tz=UTC))
            session.add(row)
        if row.at < snap:
            row.doctor_id = uuid.UUID(s["doctor_id"]) if s.get("doctor_id") else None
            row.clinic_id = uuid.UUID(s["clinic_id"]) if s.get("clinic_id") else None
            row.specialization_id = uuid.UUID(s["specialization_id"])
            row.name, row.name_ru, row.place = s["name"], s.get("name_ru", ""), s["place"]
            row.price_tiyin, row.is_active, row.at = int(s["price_tiyin"]), True, snap
    owned = await repo.active_services(
        session, doctor_ids=[doctor_id] if doctor_id else [], clinic_ids=[clinic_id] if clinic_id else []
    )
    for row in owned:
        if (
            row.id not in seen
            and row.at < snap
            and (row.doctor_id == doctor_id if doctor_id else row.doctor_id is None)
        ):
            row.is_active, row.at = False, snap


async def _apply_clinic(session: AsyncSession, item: dict[str, Any], snap: datetime) -> uuid.UUID:
    c = await repo.lock_clinic(session, uuid.UUID(item["clinic_id"]))
    if c.info_at < snap:
        c.name, c.city, c.street = item["name"], item["city"], item["street"]
        c.lat, c.lng, c.info_at = float(item["latitude"]), float(item["longitude"]), snap
    if c.status_at < snap:
        c.status, c.status_at = item["status"], snap
    if c.rating_at < snap:
        c.rating, c.reviews_count, c.rating_at = item["rating"], int(item["reviews_count"]), snap
    await _apply_services(session, item["services"], snap, clinic_id=c.id)
    return c.id


async def sync_specializations(factory: async_sessionmaker[AsyncSession], catalog: CatalogClient) -> int:
    items = await catalog.specializations()
    async with factory() as session, session.begin():
        return await repo.upsert_specializations(session, items)


async def _start(factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> datetime | None:
    es = elastic.get_es()
    await elastic.ensure_indices(es)
    async with factory() as session, session.begin():
        run = await repo.get_run(session, run_id, for_update=True)
        if run is None or run.status != "pending":
            return None
        names = {}
        for kind in (elastic.DOCTORS, elastic.CLINICS):
            names[kind] = elastic.index_name(kind, _next_version(await elastic.current_index(es, kind)))
            await es.indices.delete(index=names[kind], ignore_unavailable=True)  # oldingi yiqilgan urinish qoldig'i
            await elastic.create_index(es, kind, names[kind])
        run.status, run.started_at = "running", datetime.now(UTC)
        run.doctors_index, run.clinics_index = names[elastic.DOCTORS], names[elastic.CLINICS]
        run.snapshot_at = datetime.now(UTC)
        return run.snapshot_at


async def execute(factory: async_sessionmaker[AsyncSession], catalog: CatalogClient, run_id: uuid.UUID) -> str:
    snap = await _start(factory, run_id)
    if snap is None:
        return "skipped"
    es = elastic.get_es()
    page_size = get_settings().reindex_page_size
    try:
        await sync_specializations(factory, catalog)
        seen_doctors: set[uuid.UUID] = set()
        async for page in catalog.doctors(page_size):
            async with factory() as session, session.begin():
                for item in page:
                    seen_doctors.add(await _apply_doctor(session, item, snap))
        seen_clinics: set[uuid.UUID] = set()
        async for page in catalog.clinics(page_size):
            async with factory() as session, session.begin():
                for item in page:
                    seen_clinics.add(await _apply_clinic(session, item, snap))
        async with factory() as session, session.begin():
            removed = await repo.mark_missing_doctors(session, seen_doctors, snap)
            removed_clinics = await repo.mark_missing_clinics(session, seen_clinics, snap)
        counts = {}
        for kind, ids_of in ((elastic.DOCTORS, repo.all_doctor_ids), (elastic.CLINICS, repo.all_clinic_ids)):
            async with factory() as session:
                ids = await ids_of(session)
            for start in range(0, len(ids), BATCH):
                async with factory() as session, session.begin():
                    batch = ids[start : start + BATCH]
                    if kind == elastic.DOCTORS:
                        await documents.publish(session, doctor_ids=batch)
                    else:
                        await documents.publish(session, clinic_ids=batch)
            counts[kind] = len(ids)
        async with factory() as session, session.begin():
            run = await repo.get_run(session, run_id, for_update=True)
            assert run is not None
            for kind, name in ((elastic.DOCTORS, run.doctors_index), (elastic.CLINICS, run.clinics_index)):
                await es.indices.refresh(index=name)
                await elastic.swap_alias(es, kind, name)
            run.status, run.finished_at = "done", datetime.now(UTC)
            run.doctors_count, run.clinics_count = counts[elastic.DOCTORS], counts[elastic.CLINICS]
        logger.info(
            "reindex done",
            extra={
                "run_id": str(run_id),
                **counts,
                "removed_doctors": len(removed),
                "removed_clinics": len(removed_clinics),
            },
        )
        REINDEX_RUNS.labels(status="done").inc()
        return "done"
    except Exception as exc:
        logger.exception("reindex failed", extra={"run_id": str(run_id)})
        async with factory() as session, session.begin():
            run = await repo.get_run(session, run_id, for_update=True)
            if run is not None:
                for kind, name in ((elastic.DOCTORS, run.doctors_index), (elastic.CLINICS, run.clinics_index)):
                    # Alias allaqachon yangisiga o'tgan bo'lsa (masalan, clinics almashinuvi yiqildi) — tegilmaydi
                    if name and await elastic.current_index(es, kind) != name:
                        await es.indices.delete(index=name, ignore_unavailable=True)
                run.status, run.error, run.finished_at = "failed", str(exc)[:2000], datetime.now(UTC)
        REINDEX_RUNS.labels(status="failed").inc()
        return "failed"


async def run_pending(factory: async_sessionmaker[AsyncSession], catalog: CatalogClient) -> str | None:
    async with factory() as session, session.begin():
        run = await repo.next_pending(session)
        run_id = run.id if run else None
    if run_id is None:
        return None
    return await execute(factory, catalog, run_id)
