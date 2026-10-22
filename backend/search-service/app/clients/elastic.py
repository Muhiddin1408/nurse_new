"""Elasticsearch 8: client, indeks mapping'lari va alias boshqaruvi.

O'qish va yozish ALIAS orqali (`doctors`, `clinics`); fizik indeks — `doctors_v<N>`. Reindex yangi versiyani
quradi va alias'ni bitta `update_aliases` bilan atomik almashtiradi (o'qish uzilmaydi).
Yozish — `version_type=external` (`doc_version`): eski hujjat yangisini ustidan yozmaydi (409 — e'tiborsiz).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from elasticsearch import AsyncElasticsearch, BadRequestError, ConflictError, NotFoundError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DOCTORS, CLINICS = "doctors", "clinics"

ANALYSIS: dict[str, Any] = {
    "analyzer": {
        # O'zbekcha/ruscha (lotin + kirill) ismlar: katta-kichik harf va diakritikadan mustaqil
        "name_analyzer": {"type": "custom", "tokenizer": "standard", "filter": ["lowercase", "asciifolding"]},
    }
}

TEXT = {"type": "text", "analyzer": "name_analyzer"}

DOCTOR_MAPPING: dict[str, Any] = {
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "full_name": {
            **TEXT,
            "fields": {
                "suggest": {"type": "search_as_you_type", "analyzer": "name_analyzer"},
                "raw": {"type": "keyword"},
            },
        },
        "photo_file_id": {"type": "keyword", "index": False},
        "specialization_ids": {"type": "keyword"},
        "specialization_names": TEXT,
        "service_names": TEXT,
        "experience_years": {"type": "integer"},
        "languages": {"type": "keyword"},
        "rating": {"type": "float"},
        "reviews_count": {"type": "integer"},
        "accepts_home_visits": {"type": "boolean"},
        "home_visit_radius_km": {"type": "integer"},
        "home_base": {"type": "geo_point"},
        "places": {"type": "keyword"},
        "min_price_tiyin": {"type": "long"},
        "clinic_ids": {"type": "keyword"},
        "clinics": {
            "type": "object",
            "enabled": False,  # faqat javobda ko'rsatish uchun (id, name, city)
        },
        "cities": {"type": "keyword", "normalizer": "lower"},
        # Klinikalar joylashuvi — geo filtr/saralash istalgan biriga
        "locations": {"type": "geo_point"},
        "next_available_at": {"type": "date"},
        "is_bookable": {"type": "boolean"},
        "status": {"type": "keyword"},
    },
}

CLINIC_MAPPING: dict[str, Any] = {
    "dynamic": "strict",
    "properties": {
        "id": {"type": "keyword"},
        "name": {
            **TEXT,
            "fields": {
                "suggest": {"type": "search_as_you_type", "analyzer": "name_analyzer"},
                "raw": {"type": "keyword"},
            },
        },
        "city": {"type": "keyword", "normalizer": "lower"},
        "city_text": TEXT,
        "street": {"type": "text", "index": False},
        "location": {"type": "geo_point"},
        "rating": {"type": "float"},
        "reviews_count": {"type": "integer"},
        "specialization_ids": {"type": "keyword"},
        "specialization_names": TEXT,
        "service_names": TEXT,
        "doctors_count": {"type": "integer"},
        "min_price_tiyin": {"type": "long"},
        "is_active": {"type": "boolean"},
        "status": {"type": "keyword"},
    },
}

MAPPINGS = {DOCTORS: DOCTOR_MAPPING, CLINICS: CLINIC_MAPPING}


def alias(kind: str) -> str:
    return f"{get_settings().index_prefix}{kind}"


def index_name(kind: str, version: int) -> str:
    return f"{alias(kind)}_v{version}"


def index_body(kind: str) -> dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,  # bitta tugun (dev/staging); prod klasterida admin o'zgartiradi
            "analysis": {
                **ANALYSIS,
                "normalizer": {"lower": {"type": "custom", "filter": ["lowercase", "asciifolding"]}},
            },
        },
        "mappings": MAPPINGS[kind],
    }


@lru_cache(maxsize=1)
def get_es() -> AsyncElasticsearch:
    s = get_settings()
    auth = (
        (s.elasticsearch_username, s.elasticsearch_password.get_secret_value())
        if s.elasticsearch_username and s.elasticsearch_password
        else None
    )
    return AsyncElasticsearch(s.elasticsearch_url, basic_auth=auth, request_timeout=s.elasticsearch_timeout_seconds)


async def current_index(es: AsyncElasticsearch, kind: str) -> str | None:
    try:
        found = await es.indices.get_alias(name=alias(kind))
    except NotFoundError:
        return None
    names = sorted(found.body)
    return names[-1] if names else None


async def create_index(es: AsyncElasticsearch, kind: str, name: str) -> None:
    try:
        await es.indices.create(index=name, **index_body(kind))
    except BadRequestError as exc:
        if exc.error != "resource_already_exists_exception":
            raise


async def ensure_indices(es: AsyncElasticsearch) -> None:
    """Alias yo'q bo'lsa `<kind>_v1` yaratib bog'laydi. Idempotent (bir nechta jarayon parallel chaqirishi mumkin)."""
    for kind in MAPPINGS:
        if await current_index(es, kind) is not None:
            continue
        name = index_name(kind, 1)
        await create_index(es, kind, name)
        try:
            await es.indices.put_alias(index=name, name=alias(kind))
        except BadRequestError:  # pragma: no cover — parallel jarayon allaqachon bog'ladi
            logger.info("alias already created", extra={"alias": alias(kind)})


async def swap_alias(es: AsyncElasticsearch, kind: str, new_index: str) -> str | None:
    """Alias'ni atomik almashtiradi; eski indeks o'chiriladi. Qaytaradi: eski indeks nomi."""
    old = await current_index(es, kind)
    actions: list[dict[str, Any]] = [{"add": {"index": new_index, "alias": alias(kind)}}]
    if old and old != new_index:
        actions.insert(0, {"remove": {"index": old, "alias": alias(kind)}})
    await es.indices.update_aliases(actions=actions)
    if old and old != new_index:
        await es.indices.delete(index=old, ignore_unavailable=True)
    return old


async def put(es: AsyncElasticsearch, index: str, doc_id: str, doc: dict[str, Any], version: int) -> bool:
    """External versiya bilan yozish. Eski versiya (parallel yoki takroriy yozuv) — False, xato emas."""
    try:
        await es.index(index=index, id=doc_id, document=doc, version=version, version_type="external")
    except ConflictError:
        return False
    return True


async def ping(es: AsyncElasticsearch) -> None:
    health = await es.cluster.health(timeout="2s")
    if health.body.get("status") == "red":
        raise RuntimeError("elasticsearch cluster red")
