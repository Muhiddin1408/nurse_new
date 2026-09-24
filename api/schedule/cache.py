from __future__ import annotations

import json
import logging
from datetime import date, datetime, time
from uuid import UUID

from django.core.cache import cache

from api.schedule import services as schedule_services

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30
CACHE_VERSION = "v1"  # sxema o'zgarganda oshiring — eski keshlar avtomatik "yo'qoladi"


def _cache_key(doctor_id: UUID, day: date) -> str:
    return f"slots:{CACHE_VERSION}:{doctor_id}:{day.isoformat()}"


def get_available_slots_cached(doctor_id: UUID, day: date) -> list[dict]:
    """Bir kunlik bo'sh slotlar — avval keshdan, bo'lmasa bazadan.

    Cache-aside pattern: kesh o'zi bazaga bormaydi. Biz keshni tekshiramiz,
    bo'sh bo'lsa bazadan olamiz va keshga yozamiz. Bu eng keng tarqalgan
    va eng oson tushuniladigan keshlash naqshi.
    """
    key = _cache_key(doctor_id, day)

    cached = cache.get(key)
    if cached is not None:
        return json.loads(cached)  # HIT

    # MISS — bazadan olamiz.
    # ⬇ Kun chegaralari MAHALLIY vaqtda quriladi (`tzinfo=LOCAL_TZ`), naive
    # datetime emas. Naive qoldirilsa `USE_TZ=True` da Django ogohlantirish
    # beradi va sanani UTC deb talqin qiladi — natijada 00:00–05:00 oralig'idagi
    # slotlar boshqa kunga tushib ketardi. Eng yomoni: `invalidate_doctor_day`
    # `get_slot_day` (mahalliy sana) bilan ishlagani uchun kalitlar mos
    # kelmasdi va kesh o'chirilmay qolardi.
    day_start = datetime.combine(day, time.min, tzinfo=schedule_services.LOCAL_TZ)
    day_end = datetime.combine(day, time.max, tzinfo=schedule_services.LOCAL_TZ)

    slots = schedule_services.get_available_slots(
        doctor_id=doctor_id, date_from=day_start, date_to=day_end
    )
    data = [
        {"id": str(s.id), "start_at": s.start_at.isoformat(), "end_at": s.end_at.isoformat()}
        for s in slots
    ]

    cache.set(key, json.dumps(data), timeout=CACHE_TTL_SECONDS)
    return data


def invalidate_doctor_day(doctor_id: UUID, day: date) -> None:
    """Event-based invalidatsiya: shu shifokorning shu kunidagi kesh o'chiriladi.

    CHAQIRUVCHI: `booking/services.py` dagi `_invalidate_slot_cache`, uchala
    holat o'zgarishida ham — create (FREE->HELD), confirm (HELD->BOOKED),
    cancel (-> FREE). Uchalasi ham slotning ko'rinadigan bandligini
    o'zgartiradi, ya'ni uchalasidan keyin ham kesh eskiradi.

    ⚠️ Buni unutish — eng ko'p uchraydigan keshlash bug'i. Kesh o'chirilmasa,
    band qilingan slot 30 soniyagacha "bo'sh" ko'rinib turadi. Shuning uchun
    TTL ni ham qoldiramiz — u xavfsizlik to'ri.
    """
    key = _cache_key(doctor_id, day)
    cache.delete(key)
    logger.debug("Kesh o'chirildi: %s", key)

# ---------------------------------------------------------------------------
# CACHE STAMPEDE — pik yuklamada yuzaga chiqadigan nozik muammo
# ---------------------------------------------------------------------------
#
# Muammo: mashhur shifokorning keshi eskirgan aynan o'sha lahzada, 500 ta
# so'rov bir vaqtda keladi. Hammasi "MISS" ko'radi va HAMMASI bir vaqtda
# bazaga uriladi — baza bir zumda 500 bir xil so'rovdan qulaydi. Bu
# "cache stampede" yoki "thundering herd".
#
# Yechim — lock: birinchi so'rov qulf oladi va bazaga boradi, qolganlari
# qisqa kutadi va tayyor keshdan oladi. Django cache'da add() atomik:
#
# def get_with_lock(doctor_id, day):
#     key = _cache_key(doctor_id, day)
#     cached = cache.get(key)
#     if cached is not None:
#         return json.loads(cached)
#
#     lock_key = f"lock:{key}"
#     got_lock = cache.add(lock_key, "1", timeout=10)  # atomik: faqat bittasi True oladi
#
#     if got_lock:
#         try:
#             data = _load_from_db(doctor_id, day)
#             cache.set(key, json.dumps(data), timeout=CACHE_TTL_SECONDS)
#             return data
#         finally:
#             cache.delete(lock_key)
#     else:
#         # Boshqa so'rov bazadan olyapti — biroz kutamiz va keshdan o'qiymiz
#         time.sleep(0.05)
#         cached = cache.get(key)
#         return json.loads(cached) if cached else _load_from_db(doctor_id, day)
#
# ⚠️ Buni FAZA 6 DA, k6 testida stampede'ni O'Z KO'ZINGIZ BILAN
# KO'RGANDAN KEYIN qo'shing. Muammoni ko'rmasdan yechim yozish — bu
# aynan "erta optimizatsiya" bo'ladi. Avval bitta mashhur shifokorga
# yuk bering, baza CPU'si sakraganini kuzating, keyin lock qo'shib
# farqni o'lchang.