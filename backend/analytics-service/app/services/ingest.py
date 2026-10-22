"""Ingest: `analytics-service.all-events` navbati (`#` — barcha eventlar) → ClickHouse `events_raw`.

Nega libs'dagi `EventConsumer` emas: u har eventni alohida Postgres tranzaksiyasida (`processed_events`)
ishlaydi va event turi bo'yicha handler talab qiladi. Analytics'ga esa HAMMA event kerak (`#`), yozish —
partiya bilan (`ingest_batch_size` yoki `ingest_flush_seconds` — qaysi oldin), dedup — ClickHouse'da
(`ReplacingMergeTree`, event_id). Topologiya (navbat, DLX) — o'sha libs funksiyalari bilan.

Kafolat — at-least-once: xabarlar partiya ClickHouse'ga yozilgandan KEYIN ack qilinadi. ClickHouse
ishlamasa partiya 1→2→4…`ingest_max_backoff_seconds` bilan qayta yoziladi; shu vaqtda yangi xabar olinmaydi
(prefetch to'lgan — tabiiy backpressure, navbat brokerda o'sadi). To'xtashda yozilmagani `nack(requeue)`.
Noto'g'ri envelope (JSON emas / maydonlar yo'q) — DLX'ga (`x-dead-letter-reason=invalid_envelope`), qolganlar
sxemaga qaramay yoziladi (xom tarix). `audit.recorded` — admin-service'niki, bu yerda o'tkazib yuboriladi.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractIncomingMessage, AbstractRobustConnection
from pydantic import ValidationError

from medbron_common.eventbus import MEDBRON_TOPOLOGY, Envelope, Topology, declare_service_queue
from medbron_common.eventbus.topology import declare_topology
from medbron_common.events import Events

from app.clients.clickhouse import ClickHouseError
from app.metrics import BATCH_SIZE, INGEST_ERRORS, INGESTED, LAG, SKIPPED

logger = logging.getLogger(__name__)

QUEUE = "analytics-service.all-events"
SKIP_TYPES = frozenset({Events.AUDIT_RECORDED})


class Sink(Protocol):
    async def insert(self, table: str, rows: Any) -> None: ...


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def to_row(envelope: Envelope, received_at: datetime) -> dict[str, Any]:
    return {
        "event_id": str(envelope.event_id),
        "event_type": envelope.event_type,
        "event_version": envelope.event_version,
        "producer": envelope.producer,
        "aggregate_type": envelope.aggregate_type,
        "aggregate_id": envelope.aggregate_id,
        "occurred_at": _ts(envelope.occurred_at),
        "received_at": _ts(received_at),
        "trace_id": envelope.trace_id or "",
        "data": json.dumps(envelope.data, ensure_ascii=False, separators=(",", ":")),
    }


@dataclass
class Pending:
    message: AbstractIncomingMessage
    row: dict[str, Any]
    occurred_at: datetime


class Ingestor:
    def __init__(
        self,
        sink: Sink,
        *,
        amqp_url: str,
        batch_size: int = 500,
        flush_seconds: float = 2.0,
        prefetch: int = 1000,
        max_backoff: float = 30.0,
        topology: Topology = MEDBRON_TOPOLOGY,
        queue_name: str = QUEUE,
    ) -> None:
        self.sink = sink
        self._url = amqp_url
        self.batch_size = batch_size
        self.flush_seconds = flush_seconds
        self.prefetch = max(prefetch, batch_size)
        self.max_backoff = max_backoff
        self.topology = topology
        self.queue_name = queue_name
        self.connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._dlx: AbstractExchange | None = None
        self._inbox: asyncio.Queue[AbstractIncomingMessage] = asyncio.Queue()
        self._batch: list[Pending] = []

    # ------------------------------------------------------------------ xabar → qator
    async def accept(self, message: AbstractIncomingMessage) -> None:
        """Bitta xabar: partiyaga qo'shiladi yoki (noto'g'ri / audit) darhol hal qilinadi."""
        try:
            raw = json.loads(message.body)
            if not isinstance(raw, dict):
                raise ValueError("envelope obyekt emas")
            envelope = Envelope.model_validate(raw)
        except (ValueError, ValidationError) as exc:
            await self._dead_letter(message, "invalid_envelope", exc)
            return
        if envelope.event_type in SKIP_TYPES:
            SKIPPED.labels(reason="audit").inc()
            await message.ack()
            return
        now = datetime.now(UTC)
        self._batch.append(Pending(message, to_row(envelope, now), envelope.occurred_at))

    async def flush(self, stop: asyncio.Event | None = None) -> bool:
        """Partiyani yozadi va ack qiladi. ClickHouse yiqilsa backoff bilan qayta urinadi; `stop` o'rnatilsa
        yozilmaganlarni brokerga qaytaradi (False)."""
        if not self._batch:
            return True
        batch, self._batch = self._batch, []
        delay = 1.0
        while True:
            try:
                await self.sink.insert("events_raw", [p.row for p in batch])
                break
            except ClickHouseError as exc:
                INGEST_ERRORS.inc()
                logger.warning("clickhouse insert failed, retrying", extra={"rows": len(batch), "error": str(exc)})
                if stop is not None and stop.is_set():
                    for p in batch:
                        await p.message.nack(requeue=True)
                    return False
                await _sleep_or_stop(delay, stop)
                delay = min(delay * 2, self.max_backoff)
        now = datetime.now(UTC)
        BATCH_SIZE.observe(len(batch))
        for p in batch:
            await p.message.ack()
            INGESTED.labels(event_type=p.row["event_type"]).inc()
            LAG.observe(max(0.0, (now - p.occurred_at).total_seconds()))
        return True

    async def _dead_letter(self, message: AbstractIncomingMessage, reason: str, exc: Exception) -> None:
        SKIPPED.labels(reason=reason).inc()
        logger.error("event dead-lettered", extra={"reason": reason, "error": str(exc)[:300]})
        headers = dict(message.headers or {})
        headers.update(
            {"x-dead-letter-reason": reason, "x-source-queue": self.queue_name, "x-last-error": str(exc)[:300]}
        )
        if self._dlx is None:
            await message.nack(requeue=False)  # navbatning o'z DLX'i DLQ ga olib boradi
            return
        await self._dlx.publish(
            aio_pika.Message(
                body=message.body,
                headers=headers,
                content_type=message.content_type,
                message_id=message.message_id,
                type=message.type,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=message.type or message.routing_key or "unknown",
        )
        await message.ack()

    # ------------------------------------------------------------------ hayot sikli
    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        await self._inbox.put(message)

    async def run(self, stop: asyncio.Event) -> None:
        self.connection = await aio_pika.connect_robust(self._url)
        self._channel = await self.connection.channel(publisher_confirms=True)
        await self._channel.set_qos(prefetch_count=self.prefetch)
        await declare_topology(self._channel, self.topology)
        self._dlx = await self._channel.get_exchange(self.topology.dlx)
        queue = await declare_service_queue(self._channel, self.queue_name, ["#"], self.topology)
        tag = await queue.consume(self._on_message)
        logger.info("ingest started", extra={"queue": self.queue_name, "batch": self.batch_size})
        try:
            await self._loop(stop)
        finally:
            with contextlib.suppress(Exception):
                await queue.cancel(tag)
            await self.flush(stop)
            for p in self._batch:
                await p.message.nack(requeue=True)
            while not self._inbox.empty():
                await self._inbox.get_nowait().nack(requeue=True)
            await self.connection.close()

    async def _loop(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.flush_seconds
        while not stop.is_set():
            timeout = max(0.0, deadline - loop.time())
            try:
                message = await asyncio.wait_for(self._inbox.get(), timeout=min(timeout, 0.5))
            except TimeoutError:
                message = None
            if message is not None:
                await self.accept(message)
            if len(self._batch) >= self.batch_size or loop.time() >= deadline:
                await self.flush(stop)
                deadline = loop.time() + self.flush_seconds


async def _sleep_or_stop(seconds: float, stop: asyncio.Event | None) -> None:
    if stop is None:
        await asyncio.sleep(seconds)
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
