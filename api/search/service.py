"""
search/service.py — qidiruvning TASHQI INTERFEYSI (C15.4).

`api/search/queries.py` Elasticsearch'ga so'rov yuboradi, bu fayl esa
qaror qabul qiladi: qaysi dvigatel, va u yiqilsa nima bo'ladi.

IKKI QOIDA:

1. **ES SARALAYDI, POSTGRES KO'RSATADI.**
   ES dan faqat ID'lar va tartib olinadi, ko'rsatiladigan ma'lumot
   (ism, reyting, mutaxassislik) Postgres'dan `get_doctors` orqali
   to'ldiriladi. Nega: projector biroz orqada qolsa (Kafka lagi), ES'dagi
   nusxa eskirgan bo'ladi — mijoz o'chirilgan shifokorni yoki eski
   reytingni ko'rardi. Bitta qo'shimcha SELECT (20 ta ID bo'yicha, indeksli)
   bu xavfni butunlay yo'q qiladi.

   Qo'shimcha foyda: javob shakli katalog ro'yxati bilan BIR XIL — ilova
   uchun ikkita turli DTO yo'q.

2. **ES YIQILSA, QIDIRUV YO'QOLMAYDI.**
   Elasticsearch — qulaylik, katalog esa mahsulotning o'zi. ES javob
   bermasa (ulanish yo'q, indeks yo'q, timeout) so'rov Postgres'ga
   tushadi: sekinroq va imlo xatosini kechirmaydi, lekin ishlaydi.

   Javobda `engine` maydoni bor — ilova ham, monitoring ham tizim
   degradatsiyada ekanini KO'RADI. Jim fallback eng yomon variant: ES bir
   oy o'lik turadi va buni hech kim bilmaydi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from api.catalog import services as catalog_services

logger = logging.getLogger(__name__)

ELASTICSEARCH = "elasticsearch"
POSTGRES = "postgres"


@dataclass(frozen=True)
class SearchResult:
    engine: str
    total: int
    doctors: list


def search(
    *,
    text: str = "",
    specialization_id=None,
    home_visits_only: bool = False,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float = 15,
    min_rating: float | None = None,
    sort: str = "relevance",
    limit: int = 20,
    offset: int = 0,
) -> SearchResult:
    try:
        return _via_elasticsearch(
            text=text, specialization_id=specialization_id, home_visits_only=home_visits_only,
            lat=lat, lng=lng, radius_km=radius_km, min_rating=min_rating, sort=sort,
            limit=limit, offset=offset,
        )
    except Exception:  # noqa: BLE001 — qaysi xato bo'lishidan qat'i nazar, qidiruv ishlashi kerak
        # `exception` (traceback bilan): bu holat NORMAL emas, tekshirilishi
        # shart. Alert `medbron_search_requests_total{engine="postgres"}` dan.
        logger.exception("Elasticsearch javob bermadi — Postgres'ga tushildi")
        return _via_postgres(
            text=text, specialization_id=specialization_id, home_visits_only=home_visits_only,
            lat=lat, lng=lng, radius_km=radius_km, min_rating=min_rating,
            limit=limit, offset=offset,
        )


def _via_elasticsearch(*, text, specialization_id, home_visits_only, lat, lng, radius_km,
                       min_rating, sort, limit, offset) -> SearchResult:
    from api.search.queries import search_doctors

    hit = search_doctors(
        text=text or None,
        specialization_id=str(specialization_id) if specialization_id else None,
        home_visits_only=home_visits_only,
        near_lat=lat, near_lon=lng, max_distance_km=int(radius_km),
        min_rating=min_rating, sort=sort, limit=limit, offset=offset,
    )
    ids = [row["id"] for row in hit["results"]]
    _record(ELASTICSEARCH)
    return SearchResult(engine=ELASTICSEARCH, total=hit["total"], doctors=_hydrate(ids))


def _via_postgres(*, text, specialization_id, home_visits_only, lat, lng, radius_km,
                  min_rating, limit, offset) -> SearchResult:
    doctors = catalog_services.list_doctors(
        specialization_id=specialization_id, home_visits_only=home_visits_only,
        lat=lat, lng=lng, radius_km=radius_km, text=text, min_rating=min_rating,
        limit=limit, offset=offset,
    )
    _record(POSTGRES)
    # `total` YO'Q (None): sahifalangan so'rovda umumiy sonni bilish uchun
    # alohida `COUNT(*)` kerak bo'lardi — zaxira yo'lda bu ortiqcha yuk.
    # Ilova "yana bormi" degan savolga `len(results) == limit` bilan javob
    # beradi.
    return SearchResult(engine=POSTGRES, total=None, doctors=doctors)


def _hydrate(ids: list[str]) -> list:
    """ES tartibini SAQLAB, ma'lumotni Postgres'dan to'ldiradi.

    ES'da bor, lekin bazada yo'q (yoki moderatsiyadan chiqarilgan — A9)
    shifokor ro'yxatga TUSHMAYDI: `get_doctors` faqat `approved` va faol
    foydalanuvchilarni qaytaradi."""
    if not ids:
        return []
    by_id = {str(d.id): d for d in catalog_services.get_doctors(ids)}
    return [by_id[i] for i in ids if i in by_id]


def _record(engine: str) -> None:
    from api.observability import metrics

    metrics.search_requests.labels(engine=engine).inc()
