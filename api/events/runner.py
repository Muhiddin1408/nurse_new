"""
events/runner.py — uzoq yashaydigan worker jarayonlari uchun umumiy yordamchilar

Kafka consumer'lar va outbox publisher shu yerdagi `GracefulStop` bilan
ishlaydi.

NEGA KERAK: Kubernetes podni to'xtatganda (deploy, scale-down) avval SIGTERM
yuboradi, 30 soniyadan keyin SIGKILL. Python SIGTERM'ni default holatda
USHLAMAYDI — jarayon darhol o'ladi va `finally` bloklari ISHLAMAYDI:
consumer group'dan chiqilmaydi (rebalance sekinlashadi), analytics buferi
yozilmay yo'qoladi. Signalni ushlab, siklni navbatdagi aylanishda toza
to'xtatamiz.
"""

from __future__ import annotations

import logging
import signal
import threading

logger = logging.getLogger(__name__)


class GracefulStop:
    def __init__(self, install_signals: bool = True):
        self._event = threading.Event()
        if install_signals:
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, self._handle)

    def _handle(self, signum, frame):  # noqa: ARG002
        logger.info("Signal %s olindi — toza to'xtatilmoqda", signum)
        self._event.set()

    def set(self) -> None:
        self._event.set()

    def __bool__(self) -> bool:
        """`while not stop:` deb yozish uchun."""
        return self._event.is_set()

    def wait(self, seconds: float) -> None:
        """`time.sleep` o'rniga — signal kelsa darhol uyg'onadi."""
        self._event.wait(seconds)


class NeverStop:
    """Testlar va eski chaqiruvlar uchun: hech qachon to'xtamaydi."""

    def __bool__(self) -> bool:
        return False

    def wait(self, seconds: float) -> None:
        import time

        time.sleep(seconds)


# E5: DOIMIY xatolar — qayta o'qish natijani o'zgartirmaydi -> DLQ + commit.
# Qolgan hamma narsa (tarmoq, baza, ClickHouse/ES o'chgan) VAQTINCHALIK ->
# istisno ko'tariladi, commit bo'lmaydi, xabar qayta o'qiladi.
# Ikkalasini bir xil ushlash — eng keng tarqalgan xato: yo xabar yo'qoladi,
# yo partition abadiy bloklanadi.
from django.core.exceptions import ValidationError  # noqa: E402

# ValidationError — masalan event_id UUID emas: bazaga yozishda har safar yiqiladi
PERMANENT_ERRORS = (KeyError, ValueError, TypeError, ArithmeticError, ValidationError)


def dead_letter(consumer: str, msg, error: Exception | str, envelope: dict | None = None) -> None:
    from api.observability import metrics
    from apps.utils.models import DeadLetterEvent

    raw = msg.value()
    DeadLetterEvent.objects.create(
        consumer=consumer,
        topic=msg.topic() or "",
        partition=msg.partition(),
        offset=msg.offset(),
        event_id=str((envelope or {}).get("event_id", ""))[:64],
        event_type=str((envelope or {}).get("event_type", ""))[:100],
        raw=raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw),
        error=repr(error)[:4000],
    )
    metrics.consumer_poison.labels(consumer=consumer).inc()
    logger.error(
        "Buzuq xabar DLQ ga yozildi: consumer=%s topic=%s partition=%s offset=%s xato=%r",
        consumer, msg.topic(), msg.partition(), msg.offset(), error,
    )


def decode_envelope(msg, consumer: str | None = None) -> dict | None:
    """Kafka xabarini ochadi. Buzuq xabar -> `None`.

    ZAHARLI XABAR (poison message): JSON emas yoki `event_id`/`event_type`/`data`
    yo'q. Uni qayta o'qish natijani o'zgartirmaydi — commit qilinmasa consumer
    shu xabarda abadiy aylanib qoladi va undan keyingi BARCHA xabarlar kutadi.
    Shuning uchun o'tkazib yuboramiz (offset siljiydi), lekin DLQ ga yozib.
    """
    import json

    try:
        envelope = json.loads(msg.value())
        envelope["event_id"], envelope["event_type"], envelope["data"]  # noqa: B018
        return envelope
    except (ValueError, TypeError, KeyError) as exc:
        if consumer:
            dead_letter(consumer, msg, exc)
        else:
            logger.error(
                "Buzuq xabar o'tkazib yuborildi: topic=%s partition=%s offset=%s xato=%r",
                msg.topic(), msg.partition(), msg.offset(), exc,
            )
        return None
