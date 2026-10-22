"""Ommaviy qidiruv: Elasticsearch (alias orqali); ES javob bermasa — Postgres proyeksiyasi (monolit C15.4).

- `filter` (aniq moslik, keshlanadi, ball yo'q) va `must` (relevantlik) ajratilgan.
- Geo: klinikada — klinika joylashuvi `radius_km` ichida; uyda — mijoz manzili SHIFOKORNING o'z radiusi
  (`home_visit_radius_km`) ichida (har hujjatda boshqa radius — script filtr). Joy berilmasa — ikkalasidan biri.
- `available_on` — "shu kungacha (shu kun ham) bo'sh vaqti bor": `next_available_at ≤ kun oxiri`. Aniq kunlik
  bo'shliq eventlarda yo'q (`schedule.availability_changed.dates` — o'zgargan kunlar) — ochiq savol.
- Zaxira (`engine=postgres`): faqat matn va mutaxassislik bo'yicha, reyting tartibida, `total=None`, `degraded=true`.
  Jim fallback eng yomon variant — javobda va `search_requests_total{engine}` metrikasida ko'rinadi.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import elastic
from app.core.config import get_settings
from app.metrics import LATENCY, REQUESTS
from app.models import ClinicRow, DoctorRow
from app.models.projection import APPROVED
from app.repositories import projection as repo

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Tashkent")
ELASTICSEARCH, POSTGRES = "elasticsearch", "postgres"
HOME_WITHIN_RADIUS = (
    "doc['home_base'].size() > 0 && doc['home_visit_radius_km'].size() > 0 && "
    "doc['home_base'].arcDistance(params.lat, params.lon) <= doc['home_visit_radius_km'].value * 1000.0"
)
DOCTOR_FIELDS = [
    "id", "full_name", "photo_file_id", "specialization_ids", "specialization_names", "experience_years", "languages",
    "rating", "reviews_count", "accepts_home_visits", "places", "min_price_tiyin", "clinics", "next_available_at",
    "home_base", "locations",
]  # fmt: skip


@dataclass(frozen=True)
class DoctorQuery:
    q: str = ""
    specialization: UUID | None = None
    city: str = ""
    place: str | None = None
    lat: float | None = None
    lng: float | None = None
    radius_km: float = 15
    available_on: date | None = None
    sort: str = "relevance"
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class ClinicQuery:
    q: str = ""
    specialization: UUID | None = None
    city: str = ""
    lat: float | None = None
    lng: float | None = None
    radius_km: float = 15
    sort: str = "relevance"
    limit: int = 20
    offset: int = 0


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _distance(points: list[dict[str, float] | None], lat: float | None, lng: float | None) -> float | None:
    real = [p for p in points if p]
    if lat is None or lng is None or not real:
        return None
    return round(min(_km(lat, lng, p["lat"], p["lon"]) for p in real), 2)


def _text(q: str, fields: list[str]) -> dict[str, Any]:
    return {"multi_match": {"query": q, "fields": fields, "fuzziness": "AUTO", "operator": "and"}}


def doctor_body(query: DoctorQuery) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [{"term": {"is_bookable": True}}]
    must: list[dict[str, Any]] = []
    if query.specialization:
        filters.append({"term": {"specialization_ids": str(query.specialization)}})
    if query.city:
        filters.append({"term": {"cities": query.city}})
    if query.place:
        filters.append({"term": {"places": query.place}})
    if query.available_on:
        day_end = datetime.combine(query.available_on + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_TZ)
        filters.append({"range": {"next_available_at": {"lt": day_end.isoformat()}}})
    if query.lat is not None and query.lng is not None:
        point = {"lat": query.lat, "lon": query.lng}
        clinic_near = {"geo_distance": {"distance": f"{query.radius_km}km", "locations": point}}
        home_near = {"script": {"script": {"source": HOME_WITHIN_RADIUS, "params": point}}}
        if query.place == "home":
            filters.append(home_near)
        elif query.place == "clinic":
            filters.append(clinic_near)
        else:
            filters.append({"bool": {"should": [clinic_near, home_near], "minimum_should_match": 1}})
    if query.q:
        must.append(_text(query.q, ["full_name^3", "specialization_names^2", "service_names"]))
    return {
        "query": {"bool": {"filter": filters, "must": must or [{"match_all": {}}]}},
        "from_": query.offset,
        "size": query.limit,
        "sort": _doctor_sort(query),
        "source_includes": DOCTOR_FIELDS,
        "track_total_hits": True,
    }


def _doctor_sort(query: DoctorQuery) -> list[Any]:
    if query.sort == "distance" and query.lat is not None and query.lng is not None:
        point = {"lat": query.lat, "lon": query.lng}
        return [{"_geo_distance": {"locations": point, "order": "asc", "unit": "km", "ignore_unmapped": True}}]
    if query.sort == "rating":
        return [{"rating": {"order": "desc", "missing": "_last"}}, {"reviews_count": "desc"}]
    if query.sort == "price":
        return [{"min_price_tiyin": {"order": "asc", "missing": "_last"}}]
    if query.sort == "availability":
        return [{"next_available_at": {"order": "asc", "missing": "_last"}}, "_score"]
    return ["_score", {"rating": {"order": "desc", "missing": "_last"}}, {"full_name.raw": "asc"}]


def doctor_card(src: dict[str, Any], lat: float | None, lng: float | None) -> dict[str, Any]:
    points = [*(src.get("locations") or []), src.get("home_base")]
    card = {k: src.get(k) for k in DOCTOR_FIELDS if k not in ("home_base", "locations")}
    card["distance_km"] = _distance(points, lat, lng)
    return card


def clinic_body(query: ClinicQuery) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [{"term": {"is_active": True}}]
    must: list[dict[str, Any]] = []
    if query.specialization:
        filters.append({"term": {"specialization_ids": str(query.specialization)}})
    if query.city:
        filters.append({"term": {"city": query.city}})
    if query.lat is not None and query.lng is not None:
        point = {"lat": query.lat, "lon": query.lng}
        filters.append({"geo_distance": {"distance": f"{query.radius_km}km", "location": point}})
    if query.q:
        must.append(_text(query.q, ["name^3", "specialization_names^2", "service_names", "city_text"]))
    if query.sort == "distance" and query.lat is not None and query.lng is not None:
        sort: list[Any] = [{"_geo_distance": {"location": {"lat": query.lat, "lon": query.lng}, "order": "asc"}}]
    elif query.sort == "rating":
        sort = [{"rating": {"order": "desc", "missing": "_last"}}, {"reviews_count": "desc"}]
    elif query.sort == "price":
        sort = [{"min_price_tiyin": {"order": "asc", "missing": "_last"}}]
    else:
        sort = ["_score", {"rating": {"order": "desc", "missing": "_last"}}, {"name.raw": "asc"}]
    return {
        "query": {"bool": {"filter": filters, "must": must or [{"match_all": {}}]}},
        "from_": query.offset,
        "size": query.limit,
        "sort": sort,
        "source_excludes": ["city_text", "is_active", "status", "service_names"],
        "track_total_hits": True,
    }


async def _cached(redis: Any | None, key: str) -> dict[str, Any] | None:
    if redis is None or get_settings().search_cache_seconds <= 0:
        return None
    try:
        raw = await redis.get(key)
    except Exception:  # kesh — qulaylik, xatosi qidiruvni to'xtatmaydi
        return None
    return json.loads(raw) if raw else None


async def _store(redis: Any | None, key: str, value: dict[str, Any]) -> None:
    ttl = get_settings().search_cache_seconds
    if redis is None or ttl <= 0:
        return
    try:
        await redis.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        logger.warning("search cache write failed", exc_info=True)


def _key(kind: str, query: Any) -> str:
    raw = json.dumps(asdict(query), sort_keys=True, default=str)
    return f"search:{kind}:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


async def search_doctors(session: AsyncSession, redis: Any | None, query: DoctorQuery) -> dict[str, Any]:
    started = time.monotonic()
    key = _key("doctors", query)
    hit = await _cached(redis, key)
    if hit is not None:
        return hit
    try:
        resp = await elastic.get_es().search(index=elastic.alias(elastic.DOCTORS), **doctor_body(query))
    except Exception:  # qaysi xato bo'lishidan qat'i nazar qidiruv ishlashi kerak
        logger.exception("elasticsearch unavailable — doctors search falls back to postgres")
        REQUESTS.labels(kind="doctors", engine=POSTGRES).inc()
        return await _doctors_fallback(session, query)
    result = {
        "engine": ELASTICSEARCH,
        "degraded": False,
        "total": resp["hits"]["total"]["value"],
        "items": [doctor_card(h["_source"], query.lat, query.lng) for h in resp["hits"]["hits"]],
    }
    REQUESTS.labels(kind="doctors", engine=ELASTICSEARCH).inc()
    LATENCY.labels(kind="doctors").observe(time.monotonic() - started)
    await _store(redis, key, result)
    return result


async def _doctors_fallback(session: AsyncSession, query: DoctorQuery) -> dict[str, Any]:
    stmt = select(DoctorRow).where(DoctorRow.status == APPROVED, DoctorRow.full_name != "")
    if query.q:
        stmt = stmt.where(DoctorRow.full_name.ilike(f"%{_like(query.q)}%"))
    if query.specialization:
        stmt = stmt.where(cast(DoctorRow.specialization_ids, String).contains(str(query.specialization)))
    stmt = stmt.order_by(DoctorRow.rating.desc().nulls_last(), DoctorRow.id).limit(query.limit).offset(query.offset)
    rows = (await session.scalars(stmt)).all()
    specs = {
        str(s.id): s
        for s in await repo.specializations_by_ids(session, sorted({x for d in rows for x in d.specialization_ids}))
    }
    items = [
        {
            "id": str(d.id),
            "full_name": d.full_name,
            "photo_file_id": str(d.photo_file_id) if d.photo_file_id else None,
            "specialization_ids": list(d.specialization_ids),
            "specialization_names": [specs[x].name for x in d.specialization_ids if x in specs],
            "experience_years": d.experience_years,
            "languages": list(d.languages),
            "rating": d.rating,
            "reviews_count": d.reviews_count,
            "accepts_home_visits": d.accepts_home_visits,
            "places": None,
            "min_price_tiyin": None,
            "clinics": None,
            "next_available_at": d.next_available_at.isoformat() if d.next_available_at else None,
            "distance_km": None,
        }
        for d in rows
    ]
    return {"engine": POSTGRES, "degraded": True, "total": None, "items": items}


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


async def search_clinics(session: AsyncSession, redis: Any | None, query: ClinicQuery) -> dict[str, Any]:
    started = time.monotonic()
    key = _key("clinics", query)
    hit = await _cached(redis, key)
    if hit is not None:
        return hit
    try:
        resp = await elastic.get_es().search(index=elastic.alias(elastic.CLINICS), **clinic_body(query))
    except Exception:
        logger.exception("elasticsearch unavailable — clinics search falls back to postgres")
        REQUESTS.labels(kind="clinics", engine=POSTGRES).inc()
        return await _clinics_fallback(session, query)
    items = []
    for h in resp["hits"]["hits"]:
        src = h["_source"]
        src["distance_km"] = _distance([src.get("location")], query.lat, query.lng)
        src.pop("location", None)
        items.append(src)
    result = {"engine": ELASTICSEARCH, "degraded": False, "total": resp["hits"]["total"]["value"], "items": items}
    REQUESTS.labels(kind="clinics", engine=ELASTICSEARCH).inc()
    LATENCY.labels(kind="clinics").observe(time.monotonic() - started)
    await _store(redis, key, result)
    return result


async def _clinics_fallback(session: AsyncSession, query: ClinicQuery) -> dict[str, Any]:
    stmt = select(ClinicRow).where(ClinicRow.status == "active", ClinicRow.name != "")
    if query.q:
        stmt = stmt.where(ClinicRow.name.ilike(f"%{_like(query.q)}%"))
    if query.city:
        stmt = stmt.where(ClinicRow.city.ilike(_like(query.city)))
    stmt = stmt.order_by(ClinicRow.rating.desc().nulls_last(), ClinicRow.id).limit(query.limit).offset(query.offset)
    items = [
        {
            "id": str(c.id),
            "name": c.name,
            "city": c.city,
            "street": c.street,
            "rating": c.rating,
            "reviews_count": c.reviews_count,
            "specialization_ids": None,
            "specialization_names": None,
            "doctors_count": None,
            "min_price_tiyin": None,
            "distance_km": None,
        }
        for c in (await session.scalars(stmt)).all()
    ]
    return {"engine": POSTGRES, "degraded": True, "total": None, "items": items}


async def suggest(session: AsyncSession, q: str, limit: int = 5) -> dict[str, Any]:
    """Avtoto'ldirish: shifokor ismlari, klinikalar (ES `search_as_you_type`), mutaxassisliklar (Postgres lug'ati)."""
    specs = [
        {"id": str(s.id), "name": s.name, "name_ru": s.name_ru, "slug": s.slug}
        for s in await repo.suggest_specializations(session, q, limit)
    ]
    doctors: list[dict[str, Any]] = []
    clinics: list[dict[str, Any]] = []
    engine = ELASTICSEARCH
    try:
        es = elastic.get_es()
        prefix = ["{f}.suggest", "{f}.suggest._2gram", "{f}.suggest._3gram"]
        d = await es.search(
            index=elastic.alias(elastic.DOCTORS),
            query={"bool": {
                "filter": [{"term": {"is_bookable": True}}],
                "must": [{"multi_match": {"query": q, "type": "bool_prefix",
                                          "fields": [p.format(f="full_name") for p in prefix]}}],
            }},
            size=limit, source_includes=["id", "full_name", "specialization_names"],
        )  # fmt: skip
        doctors = [h["_source"] for h in d["hits"]["hits"]]
        c = await es.search(
            index=elastic.alias(elastic.CLINICS),
            query={"bool": {
                "filter": [{"term": {"is_active": True}}],
                "must": [{"multi_match": {"query": q, "type": "bool_prefix",
                                          "fields": [p.format(f="name") for p in prefix]}}],
            }},
            size=limit, source_includes=["id", "name", "city"],
        )  # fmt: skip
        clinics = [h["_source"] for h in c["hits"]["hits"]]
    except Exception:
        logger.exception("elasticsearch unavailable — suggest only from specializations")
        engine = POSTGRES
    REQUESTS.labels(kind="suggest", engine=engine).inc()
    return {"engine": engine, "doctors": doctors, "clinics": clinics, "specializations": specs}
