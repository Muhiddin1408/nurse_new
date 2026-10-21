"""Idempotent event consumer (BLUEPRINT 8.5).

    consumer = EventConsumer(service_name="booking-service", amqp_url=..., session_factory=...,
                             processed_model=ProcessedEvent)

    @consumer.on(Events.PAYMENT_CAPTURED)
    async def on_payment_captured(session: AsyncSession, event: Envelope) -> None:
        ...  # biznes o'zgarishi + kerak bo'lsa outbox.add_event(session, ...) — bitta tranzaksiya

Kafolatlar:
- at-least-once yetkazish + `processed_events` → har event effekti bir marta. Idempotentlik
  belgisi va biznes o'zgarishi BITTA tranzaksiyada: handler yiqilsa belgi ham yozilmaydi.
- vaqtinchalik xato (tarmoq, DB lock, boshqa har qanday kutilmagan xato) → retry navbati
  (5s → 30s → 5m), `x-retry-count` bilan; limitdan keyin DLQ.
- doimiy xato (yaroqsiz JSON, sxema mos emas, noma'lum versiya, `PermanentError`) → darhol DLQ.
- Xato HECH QACHON yutib yuborilmaydi: log qilib ack qilish = eventni jim yo'qotish (Trasso #6).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medbron_common.eventbus.envelope import Envelope
from medbron_common.eventbus.outbox import ProcessedEventMixin
from medbron_common.eventbus.topology import (
    MEDBRON_TOPOLOGY,
    RETRY_TIER_HEADER,
    Topology,
    declare_service_queue,
    declare_topology,
)
from medbron_common.events import CATALOG, EventSchemaError, validate_event
from medbron_common.metrics import CONSUMER_LAG, EVENT_HANDLE_DURATION, EVENTS_CONSUMED

logger = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, Envelope], Awaitable[None]]

RETRY_COUNT_HEADER = "x-retry-count"


class PermanentError(Exception):
    """Qayta urinishdan foyda yo'q (masalan, event ma'nosi bo'yicha qabul qilib bo'lmaydi) → DLQ."""


class ConsumerConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Registration:
    handler: Handler
    versions: frozenset[int]


class EventConsumer:
    def __init__(
        self,
        *,
        service_name: str,
        amqp_url: str,
        session_factory: async_sessionmaker[AsyncSession],
        processed_model: type[ProcessedEventMixin],
        queue_name: str | None = None,
        prefetch: int = 20,
        max_retries: int = 3,
        validate_schema: bool = True,
        enforce_catalog: bool = True,
        topology: Topology = MEDBRON_TOPOLOGY,
        processed_retention: timedelta = timedelta(days=30),
        purge_interval: float = 3600.0,
    ) -> None:
        self.service_name = service_name
        # processed_events cheksiz o'smasin. Retention broker'dagi eng uzoq qayta yetkazishdan
        # (retry 5m + DLQ replay oynasi) ancha katta bo'lishi kerak — aks holda dublikat o'tib ketadi.
        self.processed_retention = processed_retention
        self.purge_interval = purge_interval
        self.queue_name = queue_name or f"{service_name}.events"
        self._url = amqp_url
        self.session_factory = session_factory
        self.processed_model = processed_model
        self.prefetch = prefetch
        self.max_retries = max_retries
        self.validate_schema = validate_schema
        self.enforce_catalog = enforce_catalog
        self.topology = topology
        self._handlers: dict[str, _Registration] = {}
        self.connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._queue: AbstractQueue | None = None
        self._consumer_tag: str | None = None
        self._retry_exchange: AbstractExchange | None = None
        self._dlx: AbstractExchange | None = None

    # ---------------------------------------------------------------- registration
    def on(self, event_type: str, *, versions: Iterable[int] = (1,)) -> Callable[[Handler], Handler]:
        if self.enforce_catalog:
            spec = CATALOG.get(event_type)
            if spec is None:
                raise ConsumerConfigError(f"{event_type!r} katalogda yo'q")
            if self.service_name not in spec.consumers:
                # Publisher'siz / katalogda e'lon qilinmagan consumer — Trasso #4. Ishga tushmasdan yiqiladi.
                raise ConsumerConfigError(
                    f"{self.service_name} katalogda {event_type} consumer'i emas — avval catalog.yaml ni yangilang"
                )
        if event_type in self._handlers:
            raise ConsumerConfigError(f"{event_type} uchun handler allaqachon bor")

        def decorator(handler: Handler) -> Handler:
            self._handlers[event_type] = _Registration(handler, frozenset(versions))
            return handler

        return decorator

    @property
    def routing_keys(self) -> list[str]:
        return sorted(self._handlers)

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if not self._handlers:
            raise ConsumerConfigError("hech qanday handler ro'yxatdan o'tmagan")
        self.connection = await aio_pika.connect_robust(self._url)
        self._channel = await self.connection.channel(publisher_confirms=True)
        await self._channel.set_qos(prefetch_count=self.prefetch)
        await declare_topology(self._channel, self.topology)
        self._retry_exchange = await self._channel.get_exchange(self.topology.retry_exchange)
        self._dlx = await self._channel.get_exchange(self.topology.dlx)
        self._queue = await declare_service_queue(self._channel, self.queue_name, self.routing_keys, self.topology)
        self._consumer_tag = await self._queue.consume(self._on_message)
        logger.info("consumer started", extra={"queue": self.queue_name, "bindings": self.routing_keys})

    async def stop(self) -> None:
        if self._queue is not None and self._consumer_tag is not None:
            await self._queue.cancel(self._consumer_tag)
        if self.connection is not None:
            await self.connection.close()
        self._queue = self._consumer_tag = self.connection = self._channel = None

    async def purge_processed(self) -> int:
        model = self.processed_model
        cutoff = datetime.now(UTC) - self.processed_retention
        async with self.session_factory() as session, session.begin():
            result = await session.execute(delete(model).where(model.processed_at < cutoff))
        deleted = int(getattr(result, "rowcount", 0) or 0)
        if deleted:
            logger.info("processed_events purged", extra={"deleted": deleted})
        return deleted

    async def _purge_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.purge_processed()
            except Exception:
                logger.exception("processed_events purge failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.purge_interval)

    async def run(self, stop: asyncio.Event) -> None:
        await self.start()
        purger = asyncio.create_task(self._purge_loop(stop))
        try:
            await stop.wait()
        finally:
            purger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await purger
            await self.stop()

    # ---------------------------------------------------------------- processing
    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        event_type = message.type or message.routing_key or "unknown"
        try:
            raw = json.loads(message.body)
            if not isinstance(raw, dict):
                raise ValueError("envelope obyekt emas")
            envelope = Envelope.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            await self._dead_letter(message, event_type, "invalid_envelope", exc)
            return

        event_type = envelope.event_type
        registration = self._handlers.get(event_type)
        if registration is None:
            await self._dead_letter(message, event_type, "no_handler", None)
            return
        if envelope.event_version not in registration.versions:
            await self._dead_letter(message, event_type, "unknown_version", None)
            return
        if self.validate_schema:
            try:
                validate_event(raw)
            except EventSchemaError as exc:
                await self._dead_letter(message, event_type, "schema_mismatch", exc)
                return

        started = time.perf_counter()
        try:
            handled = await self._handle(registration.handler, envelope)
        except PermanentError as exc:
            await self._dead_letter(message, event_type, "permanent_error", exc)
            return
        except Exception as exc:
            await self._retry(message, event_type, exc)
            return
        finally:
            EVENT_HANDLE_DURATION.labels(event_type).observe(time.perf_counter() - started)

        await message.ack()
        EVENTS_CONSUMED.labels(event_type, "ok" if handled else "duplicate").inc()
        CONSUMER_LAG.labels(event_type).observe(max(0.0, (datetime.now(UTC) - envelope.occurred_at).total_seconds()))

    async def _handle(self, handler: Handler, envelope: Envelope) -> bool:
        """True — qayta ishlandi, False — dublikat (allaqachon ishlangan)."""
        model = self.processed_model
        async with self.session_factory() as session, session.begin():
            inserted = await session.scalar(
                insert(model)
                .values(event_id=envelope.event_id, event_type=envelope.event_type)
                .on_conflict_do_nothing(index_elements=[model.event_id])
                .returning(model.event_id)
            )
            if inserted is None:
                logger.info(
                    "duplicate event skipped",
                    extra={"event_id": str(envelope.event_id), "event_type": envelope.event_type},
                )
                return False
            await handler(session, envelope)
        return True

    def _copy(self, message: AbstractIncomingMessage, headers: dict[str, Any]) -> aio_pika.Message:
        return aio_pika.Message(
            body=message.body,
            headers=headers,
            content_type=message.content_type,
            message_id=message.message_id,
            type=message.type,
            timestamp=message.timestamp,
            app_id=message.app_id,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )

    async def _retry(self, message: AbstractIncomingMessage, event_type: str, exc: Exception) -> None:
        headers = dict(message.headers or {})
        count = _as_int(headers.get(RETRY_COUNT_HEADER))
        if count >= self.max_retries:
            await self._dead_letter(message, event_type, "retries_exhausted", exc)
            return
        tiers = self.topology.retry_tiers
        tier = tiers[min(count, len(tiers) - 1)][0]
        headers.update({RETRY_COUNT_HEADER: count + 1, RETRY_TIER_HEADER: tier, "x-last-error": _err(exc)})
        assert self._retry_exchange is not None
        try:
            # routing key = navbat nomi: TTL dan keyin default exchange FAQAT shu navbatga qaytaradi.
            await self._retry_exchange.publish(self._copy(message, headers), routing_key=self.queue_name)
        except Exception:
            logger.exception("retry publish failed, requeueing", extra={"event_type": event_type})
            await message.nack(requeue=True)
            return
        await message.ack()
        EVENTS_CONSUMED.labels(event_type, "retry").inc()
        logger.warning(
            "event handling failed, scheduled retry",
            extra={
                "event_type": event_type,
                "message_id": message.message_id,
                "retry": count + 1,
                "tier": tier,
                "error": _err(exc),
            },
        )

    async def _dead_letter(
        self, message: AbstractIncomingMessage, event_type: str, reason: str, exc: Exception | None
    ) -> None:
        headers = dict(message.headers or {})
        headers.update({"x-dead-letter-reason": reason, "x-source-queue": self.queue_name})
        if exc is not None:
            headers["x-last-error"] = _err(exc)
        EVENTS_CONSUMED.labels(event_type, "dead_letter").inc()
        # ALERT: DLQ > 0.
        logger.error(
            "event dead-lettered",
            extra={
                "event_type": event_type,
                "message_id": message.message_id,
                "reason": reason,
                "error": _err(exc) if exc else None,
            },
        )
        assert self._dlx is not None
        try:
            await self._dlx.publish(self._copy(message, headers), routing_key=event_type)
        except Exception:
            # Sababni header'ga yozib bo'lmadi — navbatning o'z DLX'i baribir DLQ ga olib boradi.
            logger.exception("dlx publish failed, falling back to queue DLX")
            await message.nack(requeue=False)
            return
        await message.ack()


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _err(exc: BaseException | None) -> str:
    return f"{type(exc).__name__}: {exc}"[:500] if exc else ""
