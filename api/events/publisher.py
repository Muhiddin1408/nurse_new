import json
import logging
import random
import time
from datetime import timedelta

from confluent_kafka import Producer
from django.db import transaction
from django.utils import timezone

from api.observability import metrics
from apps.utils.models import OutboxEvent

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1.0
BATCH_SIZE = 100
MAX_ATTEMPTS = 10
MAX_BACKOFF_SECONDS = 300


def backoff_delay(attempts: int) -> timedelta:
    """E3: 2^n soniya + jitter, 5 daqiqa bilan cheklangan.

    10 ta urinish ketma-ket 1 soniyada — bu retry emas, Kafka'ga DDoS.
    """
    return timedelta(seconds=min(2**attempts, MAX_BACKOFF_SECONDS) + random.uniform(0, 1))


def build_envelope(event: OutboxEvent) -> dict:
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "occurred_at": event.created_at.isoformat(),
        "correlation_id": event.correlation_id,
        "data": event.payload,
    }


class OutboxPublisher:
    """Outbox'ni doimiy o'qib, Kafka'ga yetkazadi.

    KAFOLAT DARAJASI: at-least-once, exactly-once EMAS.
    Producer xabarni yubordi, lekin javob kelmasdan jarayon o'lib qolsa,
    keyingi ishga tushishda o'sha xabar YANA yuboriladi. Shuning uchun
    consumer tomonida IDEMPOTENTLIK majburiy.

    KO'P NUSXA (E1): partiya `SELECT ... FOR UPDATE SKIP LOCKED` bilan olinadi —
    3 ta nusxa parallel ishlaydi va bir xil qatorni ikki marta olmaydi. Bitta
    bronning hodisalari tartibi buzilmaydi: Kafka partition kaliti `booking_id`.
    Shuning uchun `replicas: 2+` xavfsiz, bitta nusxa yiqilsa zanjir uzilmaydi.
    """

    def __init__(self, producer=None):
        self._producer = producer or Producer(
            {
                "bootstrap.servers": self._kafka_addr(),
                "acks": "all",  # barcha replika tasdiqlaguncha kutamiz — durability uchun
                "retries": 3,
                "linger.ms": 20,  # kichik xabarlarni bir necha ms kutib guruhlab yuboradi
            }
        )

    @staticmethod
    def _kafka_addr() -> str:
        from django.conf import settings

        return settings.KAFKA_BOOTSTRAP_SERVERS

    def run_forever(self, stop=None) -> None:
        from django.db import close_old_connections

        from api.events.runner import NeverStop

        stop = NeverStop() if stop is None else stop  # `or` EMAS: to'xtamagan GracefulStop falsy
        logger.info("Outbox publisher ishga tushdi")
        while not stop:
            close_old_connections()
            try:
                processed = self._process_batch()
            except Exception:  # noqa: BLE001
                logger.exception("Outbox batch ishlov berishda kutilmagan xato")
                processed = 0

            if processed == 0:
                stop.wait(POLL_INTERVAL_SECONDS)

        # Navbatdagi yetkazilmagan xabarlar yo'qolmasin
        self._producer.flush(10)
        logger.info("Outbox publisher to'xtadi")

    def _process_batch(self) -> int:
        started = time.monotonic()
        # ⬇ Qulflar flush tugaguncha ushlanadi: delivery callback'lar shu
        # tranzaksiya ichida holatni yangilaydi, keyin commit. Boshqa nusxa
        # bu qatorlarni SKIP LOCKED tufayli ko'rmaydi.
        with transaction.atomic():
            events = list(
                OutboxEvent.objects.select_for_update(skip_locked=True)
                .filter(status=OutboxEvent.Status.PENDING, next_retry_at__lte=timezone.now())
                .order_by("created_at")[:BATCH_SIZE]
            )

            for event in events:
                self._publish_one(event)

            # Barcha xabarlar yuborilganini kutamiz (blocking flush) —
            # delivery callback'lar shu yerda chaqiriladi.
            self._producer.flush(timeout=10)

        if events:
            metrics.outbox_publish_duration.observe(time.monotonic() - started)
        return len(events)

    def _publish_one(self, event: OutboxEvent) -> None:
        try:
            self._producer.produce(
                topic=event.topic,
                key=str(event.key).encode("utf-8"),
                value=json.dumps(build_envelope(event)).encode("utf-8"),
                on_delivery=lambda err, msg, ev=event: self._on_delivery(ev, err, msg),
            )
            self._producer.poll(0)

        except BufferError:
            # Producer navbati to'lgan — bu xabar KEYINGI tsiklda qayta uriniladi
            logger.warning("Kafka producer navbati to'la, keyingi tsiklga qoldirildi")

    def _on_delivery(self, event: OutboxEvent, err, msg) -> None:
        if err is not None:
            self._mark_failed(event, str(err))
            return

        OutboxEvent.objects.filter(id=event.id).update(
            status=OutboxEvent.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        metrics.outbox_published.inc()

    def _mark_failed(self, event: OutboxEvent, error: str) -> None:
        attempts = event.attempts + 1
        failed = attempts >= MAX_ATTEMPTS

        OutboxEvent.objects.filter(id=event.id).update(
            attempts=attempts,
            last_error=error[:2000],
            status=OutboxEvent.Status.FAILED if failed else OutboxEvent.Status.PENDING,
            next_retry_at=timezone.now() + backoff_delay(attempts),
        )

        if failed:
            # E3: birinchi FAILED hodisa — darhol alert. Bu normal holat emas:
            # hodisa hech kimga yetmagan. Qayta yuborish: republish_outbox
            metrics.outbox_failed.inc()
            logger.error(
                "ALERT: hodisa %s marta urinishdan keyin FAILED: %s (%s). "
                "Qayta yuborish: manage.py republish_outbox --event-id %s",
                attempts, event.id, event.event_type, event.id,
            )
