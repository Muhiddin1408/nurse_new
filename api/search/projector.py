import logging

from elasticsearch import Elasticsearch, NotFoundError

from api.events.runner import PERMANENT_ERRORS, NeverStop, dead_letter, decode_envelope
from api.search.index import create_index, DOCTORS_INDEX
from api.search.utils import _es_client, _kafka_addr

logger = logging.getLogger(__name__)

CONSUMER_NAME = "search-projector"


def run_projector(stop=None, consumer=None, es=None) -> None:
    """catalog.events'ni o'qib, ES hujjatlarini yangilaydi.

    Bu ham Faza 3 dagi consumer bilan BIR XIL naqsh: qo'lda commit,
    idempotentlik. ES hujjatini `id` bo'yicha `index` (upsert) qilamiz —
    shuning uchun bir xil hodisa ikki marta kelsa, natija bir xil bo'ladi.
    ES'da upsert tabiatan idempotent, ProcessedEvent jadvali ham shart emas.
    """
    from confluent_kafka import Consumer

    stop = NeverStop() if stop is None else stop  # `or` EMAS: to'xtamagan GracefulStop falsy
    es = es or _es_client()
    create_index(es)

    consumer = consumer or Consumer(
        {
            "bootstrap.servers": _kafka_addr(),
            "group.id": "search-projector",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(["catalog.events"])

    logger.info("Search projector ishga tushdi")
    try:
        while not stop:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logger.error("Kafka xatosi: %s", msg.error())
                continue

            envelope = decode_envelope(msg, consumer=CONSUMER_NAME)
            if envelope is not None:
                # ES xatosi (ulanish yo'q) bu yerda KO'TARILADI va jarayon
                # o'ladi — ataylab: commit qilinmaydi, Kubernetes qayta
                # ishga tushiradi va xabar qayta o'qiladi. Buzuq xabardan
                # farqli, bu vaqtinchalik muammo.
                try:
                    _project(es, envelope)
                except PERMANENT_ERRORS as exc:
                    # E5: payload buzuq (masalan `id` yo'q) — DLQ, bloklamaymiz
                    dead_letter(CONSUMER_NAME, msg, exc, envelope)
            consumer.commit(msg)
    finally:
        consumer.close()


def _project(es: Elasticsearch, envelope: dict) -> None:
    event_type = envelope["event_type"]
    data = envelope["data"]

    if event_type in ("DoctorApproved", "DoctorUpdated", "ServicePriceChanged", "DoctorRatingChanged"):
        es.index(index=DOCTORS_INDEX, id=data["id"], document=_to_es_doc(data))

    elif event_type == "DoctorSuspended":
        # O'chirmaymiz — is_bookable=False qilamiz. Shunda "nega yo'qoldi"
        # degan savolga javob bor, va kerak bo'lsa qayta yoqish oson.
        try:
            es.update(index=DOCTORS_INDEX, id=data["id"], doc={"is_bookable": False})
        except NotFoundError:
            pass


def _to_es_doc(data: dict) -> dict:
    return {
        "id": data["id"],
        "full_name": data["full_name"],
        "specializations": data.get("specialization_ids", []),
        "specialization_names": data.get("specialization_names", []),
        "experience_years": data.get("experience_years", 0),
        "rating": data.get("rating", 0),
        "reviews_count": data.get("reviews_count", 0),
        "accepts_home_visits": data.get("accepts_home_visits", False),
        "min_price": data.get("min_price", 0),
        "clinic_ids": data.get("clinic_ids", []),
        "location": data.get("location"),  # {"lat": .., "lon": ..} yoki None
        "is_bookable": data.get("is_bookable", True),
    }

