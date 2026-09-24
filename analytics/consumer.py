# ===========================================================================
from __future__ import annotations

import logging
import time
from datetime import date

from analytics.utils import _clickhouse_client, _kafka_addr
from api.events.runner import PERMANENT_ERRORS, NeverStop, dead_letter, decode_envelope

logger = logging.getLogger(__name__)

CONSUMER_NAME = "analytics-loader"

BATCH_SIZE = 1000  # ClickHouse'ni bitta-bitta qator bilan sug'ormang — u ommaviy yozishga sozlangan
FLUSH_INTERVAL_SECONDS = 5


def run_analytics_consumer(stop=None, consumer=None) -> None:
    """booking.events'ni o'qib, fact_booking'ga OMMAVIY yozadi.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  OLTP consumer'dan MUHIM FARQ: bu yerda xabarlarni BITTALAB emas,    ║
    ║  PARTIYALAB yozamiz. ClickHouse bitta INSERT'ga 1 qator yozishni     ║
    ║  yomon ko'radi — u minglab qatorni birdan yozishga optimallashgan.    ║
    ║  Har xabarda bitta INSERT qilsangiz, ClickHouse tiz cho'kadi.        ║
    ║                                                                       ║
    ║  Shuning uchun: xabarlarni buferga yig'amiz, bufer to'lganda YOKI     ║
    ║  vaqt o'tganda birdan yozamiz.                                        ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    from confluent_kafka import Consumer

    stop = NeverStop() if stop is None else stop  # `or` EMAS: to'xtamagan GracefulStop falsy
    consumer = consumer or Consumer(
        {
            "bootstrap.servers": _kafka_addr(),
            "group.id": "analytics-loader",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(["booking.events"])

    buffer: list[dict] = []
    # ⬇ `pending` — oxirgi commit'dan beri o'qilgan xabar bormi. `buffer` dan
    # alohida, chunki `BookingCreated` bo'lmagan noma'lum/buzuq xabarlar
    # buferga tushmaydi, lekin ularning offset'i ham siljishi kerak.
    pending = False
    last_flush = time.monotonic()

    logger.info("Analytics consumer ishga tushdi")
    try:
        while not stop:
            msg = consumer.poll(1.0)

            if msg is not None:
                if msg.error():
                    logger.error("Kafka xatosi: %s", msg.error())
                else:
                    pending = True
                    envelope = decode_envelope(msg, consumer=CONSUMER_NAME)
                    try:
                        row = _to_fact_row(envelope) if envelope else None
                    except PERMANENT_ERRORS as exc:
                        # E5: buzuq payload (masalan total_price=null) partitionni
                        # bloklamasin — DLQ ga, keyingi xabarga o'tamiz
                        dead_letter(CONSUMER_NAME, msg, exc, envelope)
                        row = None
                    if row:
                        buffer.append(row)

            elapsed = time.monotonic() - last_flush
            if len(buffer) >= BATCH_SIZE or (pending and elapsed >= FLUSH_INTERVAL_SECONDS):
                _flush(buffer)
                consumer.commit(asynchronous=False)  # faqat yozilgandan KEYIN offset siljiydi
                buffer, pending = [], False
                last_flush = time.monotonic()
    finally:
        # SIGTERM'da bufer yoziladi VA commit qilinadi. Ilgari commit yo'q edi:
        # qayta ishga tushganda o'sha xabarlar yana o'qilib, ClickHouse'da
        # (MergeTree dublikatni tozalamaydi) ikki marta sanalardi.
        if pending:
            _flush(buffer)
            consumer.commit(asynchronous=False)
        consumer.close()


def ensure_schema() -> None:
    """`schema.SCHEMA` dagi jadvallarni yaratadi (CREATE ... IF NOT EXISTS — idempotent).

    ClickHouse bitta so'rovda bir nechta statement qabul qilmaydi, shuning
    uchun izohlarni olib tashlab, `;` bo'yicha bo'lib yuboramiz.
    """
    from analytics.schema import FACT_TTL_DAYS, SCHEMA

    sql = "\n".join(line.split("--", 1)[0] for line in SCHEMA.splitlines())
    # E14: saqlash muddati sozlamadan keladi (`{fact_ttl_days}` o'rnini bosadi)
    sql = sql.format(fact_ttl_days=FACT_TTL_DAYS)
    client = _clickhouse_client()
    for statement in filter(None, (s.strip() for s in sql.split(";"))):
        client.command(statement)


# Hodisa turi -> `fact_booking.status`.
#
# ⚠️ `BookingExpired` va `BookingCancelled` ATAYLAB alohida:
#   cancelled = mijoz fikridan qaytdi (yoki to'lov provayderi bekor qildi)
#   expired   = mijoz umuman to'lay olmadi
# Biznes uchun bu ikki butunlay boshqa hodisa. Ilgari muddati o'tgan bron
# ham `BookingCancelled` chiqarardi, natijada `conversion_funnel` dagi
# `countIf(status = 'expired')` har doim 0 qaytarar va
# `cancellation_rate_by_specialization` sun'iy oshib turardi — "mijozlar
# bekor qilyapti" deb ko'rinardi, aslida "to'lay olmayapti".
#
# `BookingCreated` — voronkaning MAXRAJI. Usiz "nechta bron boshlandi"
# noma'lum qoladi va konversiyani umuman hisoblab bo'lmaydi: faqat
# tugagan bronlar ko'rinadi, boshlanib to'lanmaganlari esa yo'q.
EVENT_STATUS = {
    "BookingCreated": "pending_payment",
    "BookingConfirmed": "confirmed",
    "BookingCancelled": "cancelled",
    "BookingExpired": "expired",
    "BookingCompleted": "completed",
    "BookingNoShow": "no_show",
}

# ⚠️ Bitta bron `fact_booking` ga BIR NECHA qator yozadi (created, keyin
# confirmed/cancelled/expired) — bu ClickHouse'da normal, chunki
# MergeTree faqat qo'shib boradi. LEKIN so'rov yozayotganda buni yodda
# tuting: `count()` bronlar sonini EMAS, hodisalar sonini beradi.
# `analytics/queries.py` dagi so'rovlar shuning uchun holat bo'yicha
# aniq filtrlaydi.


def _to_fact_row(envelope: dict) -> dict | None:
    """Hodisani analitik qatorga o'giradi va DENORMALIZATSIYA qiladi.

    E'tibor bering: bu yerda biz specialization, city, patient_age_group
    kabi maydonlarni HODISA ICHIDAN olamiz — join qilmaymiz. Shuning uchun
    booking.events hodisasi bu maydonlarni O'Z ICHIGA OLISHI kerak
    (events.publish payload'iga qo'shiladi).

    Noma'lum hodisa turi -> `None` (o'tkazib yuboriladi). Bu ataylab:
    `booking.events` topic'iga kelajakda yangi hodisa turi qo'shilsa,
    analitika consumer'i yiqilmasligi kerak.
    """
    status = EVENT_STATUS.get(envelope["event_type"])
    if status is None:
        return None

    data = envelope["data"]

    return {
        # E4: ReplacingMergeTree dedup kaliti — Kafka rebalance'da qayta
        # o'qilgan hodisa ikkinchi qator bo'lib qolmaydi
        "event_id": envelope.get("event_id") or f"{data['booking_id']}:{envelope['event_type']}",
        "booking_id": data["booking_id"],
        # Payload'da ISO satr; ClickHouse `Date` ustuni `date` obyektini kutadi
        "created_date": date.fromisoformat(data["created_date"]),
        "doctor_id": data["doctor_id"],
        "clinic_id": data.get("clinic_id"),
        "specialization": data.get("specialization", "unknown"),
        "place": data.get("place", "clinic"),
        "city": data.get("city", "unknown"),
        "total_price": int(data.get("total_price", 0)),
        "status": status,
        # ⬇ FAQAT haqiqiy bekor qilish. Muddati o'tgan bron (`expired`) bu
        # yerda 0 bo'ladi — aks holda u bekor qilish darajasiga qo'shilib,
        # ko'rsatkichni buzardi. Muddat o'tishi `conversion_funnel` da
        # alohida ustun sifatida ko'rinadi.
        "is_cancelled": 1 if status == "cancelled" else 0,
        "is_home_visit": 1 if data.get("place") == "home" else 0,
        "patient_age_group": data.get("patient_age_group", "unknown"),
        "patient_gender": data.get("patient_gender", "unknown"),
        # A10: kohorta tahlili uchun — raqam emas, HMAC
        "client_hash": data.get("client_hash", ""),
    }


def _flush(rows: list[dict]) -> None:
    """Buferni ClickHouse'ga bitta INSERT bilan yozadi."""
    if not rows:
        return
    # clickhouse_connect `insert` dict'lar ro'yxatini EMAS, qatorlar (list) va
    # ustun nomlarini kutadi. Ilgari dict berilardi — birinchi haqiqiy
    # yozishda yiqilardi.
    columns = list(rows[0].keys())
    client = _clickhouse_client()
    client.insert("fact_booking", [[r[c] for c in columns] for r in rows], column_names=columns)
    logger.info("ClickHouse'ga %s qator yozildi", len(rows))

