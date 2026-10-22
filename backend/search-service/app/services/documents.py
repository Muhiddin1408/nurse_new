"""Proyeksiya → Elasticsearch hujjatlari. Har o'zgarishdan keyin ta'sirlangan shifokor/klinika hujjati TO'LIQ
qayta quriladi (qisman event bilan qisman update emas — yagona to'g'ri manba Postgres).

Tartib: qator qulfi (tartiblangan id) → `doc_version += 1` → hujjat → ES (`external` versiya). Parallel ikki
tranzaksiya bitta shifokorga tegsa, ikkinchisi birinchisining commit'ini kutadi va kattaroq versiya bilan
yozadi — ES'da eski holat qolmaydi. Reindex yangi indeksni to'ldirayotgan bo'lsa — ikkala indeksga yoziladi.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import elastic
from app.metrics import DOCS_WRITTEN
from app.models import ClinicRow, DoctorRow, ServiceRow
from app.models.projection import APPROVED
from app.repositories import projection as repo


def _names(specs: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for s in specs:
        out += [n for n in (s.name, s.name_ru) if n]
    return out


async def doctor_doc(session: AsyncSession, d: DoctorRow) -> dict[str, Any]:
    affiliations = await repo.affiliations_of_doctor(session, d.id)
    active_clinic_ids = sorted({a.clinic_id for a in affiliations if a.status == "active"}, key=str)
    clinics = await repo.clinics_by_ids(session, active_clinic_ids)
    # Faqat faol klinikalar: to'xtatilgan klinikada bron qabul qilinmaydi (catalog `active_clinic_ids` bilan bir xil)
    live = [clinics[c] for c in active_clinic_ids if c in clinics and clinics[c].status == "active"]
    services = await repo.active_services(session, doctor_ids=[d.id], clinic_ids=[c.id for c in live])
    offerings: list[ServiceRow] = []
    for s in services:
        if s.place == "home":
            # Uy chaqiruvi — faqat shifokorning o'z xizmati va u uy chaqiruvini qabul qilsa
            if s.doctor_id == d.id and d.accepts_home_visits:
                offerings.append(s)
        elif live:
            # Klinikada: shifokorning o'z xizmati yoki faol klinikasining xizmati — faol klinika bo'lsagina
            offerings.append(s)
    places = sorted({s.place for s in offerings})
    spec_ids = sorted(
        {*map(str, d.specialization_ids), *(str(s.specialization_id) for s in offerings if s.specialization_id)}
    )
    specs = await repo.specializations_by_ids(session, spec_ids)
    home_base = (
        {"lat": d.home_lat, "lon": d.home_lng}
        if "home" in places and d.home_lat is not None and d.home_lng is not None
        else None
    )
    return {
        "id": str(d.id),
        "full_name": d.full_name,
        "photo_file_id": str(d.photo_file_id) if d.photo_file_id else None,
        "specialization_ids": spec_ids,
        "specialization_names": _names(specs),
        "service_names": sorted({n for s in offerings for n in (s.name, s.name_ru) if n}),
        "experience_years": d.experience_years,
        "languages": list(d.languages),
        "rating": d.rating,
        "reviews_count": d.reviews_count,
        "accepts_home_visits": d.accepts_home_visits,
        "home_visit_radius_km": d.home_visit_radius_km,
        "home_base": home_base,
        "places": places,
        "min_price_tiyin": min((s.price_tiyin for s in offerings), default=None),
        "clinic_ids": [str(c.id) for c in live],
        "clinics": [{"id": str(c.id), "name": c.name, "city": c.city} for c in live],
        "cities": sorted({c.city for c in live if c.city}),
        "locations": [{"lat": c.lat, "lon": c.lng} for c in live if c.lat is not None and c.lng is not None],
        "next_available_at": d.next_available_at.isoformat() if d.next_available_at else None,
        # Bron qilsa bo'ladi: tasdiqlangan VA kamida bitta taklif (klinikada yoki uyda) bor
        "is_bookable": d.status == APPROVED and bool(d.full_name) and bool(offerings),
        "status": d.status,
    }


async def clinic_doc(session: AsyncSession, c: ClinicRow) -> dict[str, Any]:
    doctor_ids = await repo.active_doctor_ids_of_clinic(session, c.id)
    doctors = [d for d in await repo.doctors_by_ids(session, doctor_ids) if d.status == APPROVED]
    services = await repo.active_services(session, clinic_ids=[c.id], doctor_ids=[d.id for d in doctors])
    services = [s for s in services if s.place == "clinic"]
    spec_ids = sorted(
        {str(s.specialization_id) for s in services if s.specialization_id}
        | {str(x) for d in doctors for x in d.specialization_ids}
    )
    specs = await repo.specializations_by_ids(session, spec_ids)
    return {
        "id": str(c.id),
        "name": c.name,
        "city": c.city,
        "city_text": c.city,
        "street": c.street,
        "location": {"lat": c.lat, "lon": c.lng} if c.lat is not None and c.lng is not None else None,
        "rating": c.rating,
        "reviews_count": c.reviews_count,
        "specialization_ids": spec_ids,
        "specialization_names": _names(specs),
        "service_names": sorted({n for s in services for n in (s.name, s.name_ru) if n}),
        "doctors_count": len(doctors),
        "min_price_tiyin": min((s.price_tiyin for s in services), default=None),
        "is_active": c.status == "active" and bool(c.name),
        "status": c.status,
    }


async def _targets(session: AsyncSession, kind: str) -> list[str]:
    targets = [elastic.alias(kind)]
    run = await repo.running_targets(session)
    if run is not None:
        targets.append(run.doctors_index if kind == elastic.DOCTORS else run.clinics_index)
    return targets


async def publish(
    session: AsyncSession, *, doctor_ids: Iterable[uuid.UUID] = (), clinic_ids: Iterable[uuid.UUID] = ()
) -> None:
    """Chaqiruvchining tranzaksiyasida. ES xatosi — ko'tariladi (consumer retry qiladi, tranzaksiya rollback)."""
    es = elastic.get_es()
    doctors = await repo.lock_doctors(session, sorted(set(doctor_ids)))
    if doctors:
        targets = await _targets(session, elastic.DOCTORS)
        for d in doctors:
            d.doc_version += 1
            doc = await doctor_doc(session, d)
            for index in targets:
                await elastic.put(es, index, str(d.id), doc, d.doc_version)
            DOCS_WRITTEN.labels(kind="doctor").inc()
    clinics = await repo.lock_clinics(session, sorted(set(clinic_ids)))
    if clinics:
        targets = await _targets(session, elastic.CLINICS)
        for c in clinics:
            c.doc_version += 1
            doc = await clinic_doc(session, c)
            for index in targets:
                await elastic.put(es, index, str(c.id), doc, c.doc_version)
            DOCS_WRITTEN.labels(kind="clinic").inc()
