"""Brokerga publish — FAQAT outbox relay ishlatadi.

Servis kodida `publish()` ni to'g'ridan-to'g'ri chaqirish taqiqlangan (BLUEPRINT 8.4):
biznes yozuvi va event bitta tranzaksiyada `outbox_events` ga yoziladi.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractRobustConnection

from medbron_common.eventbus.topology import MEDBRON_TOPOLOGY, Topology, declare_topology


def build_message(envelope: dict[str, Any], *, traceparent: str | None = None) -> aio_pika.Message:
    headers: dict[str, Any] = {"event_version": envelope["event_version"], "producer": envelope["producer"]}
    if traceparent:
        headers["traceparent"] = traceparent
    occurred = datetime.fromisoformat(envelope["occurred_at"].replace("Z", "+00:00"))
    return aio_pika.Message(
        body=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(),
        message_id=envelope["event_id"],
        type=envelope["event_type"],
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        timestamp=occurred,
        app_id=envelope["producer"],
        headers=headers,
    )


class EventPublisher:
    def __init__(self, url: str, *, topology: Topology = MEDBRON_TOPOLOGY) -> None:
        self._url = url
        self.topology = topology
        self.connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None

    async def connect(self) -> None:
        if self.connection is not None and not self.connection.is_closed:
            return
        self.connection = await aio_pika.connect_robust(self._url)
        # publisher_confirms=True — broker diskka yozganini tasdiqlamaguncha `publish` qaytmaydi.
        self._channel = await self.connection.channel(publisher_confirms=True)
        self._exchange = await declare_topology(self._channel, self.topology)

    async def publish(self, envelope: dict[str, Any], *, traceparent: str | None = None) -> None:
        if self._exchange is None:
            await self.connect()
        assert self._exchange is not None
        await self._exchange.publish(
            build_message(envelope, traceparent=traceparent), routing_key=envelope["event_type"]
        )

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
        self.connection = self._channel = self._exchange = None
