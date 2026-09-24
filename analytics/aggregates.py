"""
analytics/aggregates.py — kunlik agregat (E14).

Dashboard `fact_booking` ni to'g'ridan-to'g'ri o'qisa, ma'lumot o'sgani sayin
sekinlashadi: har bir grafik yuz millionlab qatorni skanerlaydi. Yechim —
kichik `daily_booking_agg` jadvali, dashboard SHUNDAN o'qiydi.

NEGA MATERIALIZED VIEW EMAS, QAYTA HISOBLASH:
    MV har INSERT'da ishlaydi. `fact_booking` — ReplacingMergeTree, ya'ni
    Kafka'ning at-least-once yetkazishida TAKRORLANGAN hodisa jadvalga
    yoziladi va faqat fonda birlashtiriladi. MV o'sha takrorni ko'rib,
    daromadni ikki marta qo'shib yuborardi — va buni hech kim sezmasdi,
    chunki grafik baribir "ishonchli" ko'rinib turadi.

    Bu yerda agregat `FINAL` bilan QAYTA HISOBLANADI. Sekinroq, lekin:
      — takrorlangan hodisalarga chidamli (FINAL dedup qiladi);
      — KECH KELGAN hodisalarga chidamli (consumer bir kun to'xtab qolsa,
        keyingi yurish o'sha kunni to'g'rilab yozadi);
      — qayta ishga tushirish xavfsiz (ReplacingMergeTree eski qatorni
        almashtiradi, o'chirish kerak emas).

    Ya'ni bu job IDEMPOTENT: istalgan payt, istalgan marta ishlatish mumkin.

OYNA (`DEFAULT_DAYS`): faqat oxirgi kunlar qayta hisoblanadi. Butun tarixni
har kuni qayta hisoblash — xuddi xom jadvalni skanerlash, ya'ni muammoni
yechish o'rniga uni boshqa joyga ko'chirish.
"""

from __future__ import annotations

import logging

from analytics.utils import _clickhouse_client

logger = logging.getLogger(__name__)

# Nechta oxirgi kun qayta hisoblanadi. Consumer bir kun to'xtab qolsa ham
# yetarli zaxira bo'lsin; 7 kun ClickHouse uchun arzon.
DEFAULT_DAYS = 7

REBUILD_SQL = """
INSERT INTO daily_booking_agg
SELECT
    created_date,
    specialization,
    city,
    place,
    status,
    count()                    AS bookings,
    sum(total_price)           AS revenue,
    sum(is_cancelled)          AS cancelled,
    sum(is_home_visit)         AS home_visits,
    uniqExact(client_hash)     AS unique_clients,
    now()                      AS computed_at
FROM fact_booking FINAL
WHERE created_date >= today() - {days}
GROUP BY created_date, specialization, city, place, status
"""


def rebuild(days: int = DEFAULT_DAYS, client=None) -> int:
    """Oxirgi `days` kunning agregatini qayta hisoblaydi.

    Qaytaradi: yozilgan qator soni (kuzatuv uchun — 0 bo'lsa consumer
    hodisa yozmayapti yoki `fact_booking` bo'sh)."""
    client = client or _clickhouse_client()
    client.command(REBUILD_SQL.format(days=int(days)))
    written = client.command(
        f"SELECT count() FROM daily_booking_agg WHERE created_date >= today() - {int(days)}"
    )
    logger.info("daily_booking_agg qayta hisoblandi: %s kun, %s qator", days, written)
    return int(written)
