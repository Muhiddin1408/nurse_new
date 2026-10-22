"""RabbitMQ topologiyasi (ARCHITECTURE.md 8.1). Barcha `declare` lar idempotent.

    medbron.events (topic)  ──binding: event_type──▶  <servis>.events (quorum, DLX=medbron.dlx)
    medbron.dlx (topic)     ──#──▶  medbron.dlq
    medbron.retry (headers, retry-tier) ──▶ medbron.retry.<tier> (TTL) ──DLX ""──▶ <servis>.events

Retry xabari routing key = CONSUMER NAVBATI nomi bilan yuboriladi; TTL tugagach default
exchange uni aynan shu navbatga qaytaradi. Asosiy exchange'ga qaytarilmaydi — aks holda
xabarni allaqachon muvaffaqiyatli ishlagan boshqa consumerlar ham qayta olardi.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

# DIQQAT: `x-` prefiksisiz. RabbitMQ headers exchange `x-*` header'larni moslashtirishda e'tiborsiz
# qoldiradi — `x-retry-tier` bilan binding hamma xabarga mos kelib, u BARCHA retry navbatlariga tushardi.
RETRY_TIER_HEADER = "retry-tier"


@dataclass(frozen=True)
class Topology:
    exchange: str = "medbron.events"
    dlx: str = "medbron.dlx"
    dlq: str = "medbron.dlq"
    retry_exchange: str = "medbron.retry"
    # (tier nomi, TTL ms). Testlar qisqa TTL bilan alohida topologiya e'lon qiladi.
    retry_tiers: tuple[tuple[str, int], ...] = (("5s", 5_000), ("30s", 30_000), ("5m", 300_000))
    quorum: bool = True

    def retry_queue(self, tier: str) -> str:
        return f"{self.retry_exchange}.{tier}"


MEDBRON_TOPOLOGY = Topology()


async def declare_topology(channel: AbstractChannel, topology: Topology = MEDBRON_TOPOLOGY) -> AbstractExchange:
    """Umumiy exchange/DLX/DLQ/retry ni e'lon qiladi. Qaytaradi: asosiy exchange."""
    exchange = await channel.declare_exchange(topology.exchange, aio_pika.ExchangeType.TOPIC, durable=True)
    dlx = await channel.declare_exchange(topology.dlx, aio_pika.ExchangeType.TOPIC, durable=True)
    dlq_args: dict[str, Any] = {"x-queue-type": "quorum"} if topology.quorum else {}
    dlq = await channel.declare_queue(topology.dlq, durable=True, arguments=dlq_args)
    await dlq.bind(dlx, routing_key="#")

    retry = await channel.declare_exchange(topology.retry_exchange, aio_pika.ExchangeType.HEADERS, durable=True)
    for tier, ttl_ms in topology.retry_tiers:
        queue = await channel.declare_queue(
            topology.retry_queue(tier),
            durable=True,
            arguments={"x-message-ttl": ttl_ms, "x-dead-letter-exchange": ""},
        )
        await queue.bind(retry, arguments={"x-match": "all", RETRY_TIER_HEADER: tier})
    return exchange


async def declare_service_queue(
    channel: AbstractChannel,
    queue_name: str,
    routing_keys: Iterable[str],
    topology: Topology = MEDBRON_TOPOLOGY,
) -> AbstractQueue:
    """Servisning YAGONA navbati va uning binding'lari (BLUEPRINT 8.2)."""
    exchange = await channel.declare_exchange(topology.exchange, aio_pika.ExchangeType.TOPIC, durable=True)
    arguments: dict[str, Any] = {"x-dead-letter-exchange": topology.dlx}
    if topology.quorum:
        arguments["x-queue-type"] = "quorum"
    queue = await channel.declare_queue(queue_name, durable=True, arguments=arguments)
    for key in sorted(set(routing_keys)):
        await queue.bind(exchange, routing_key=key)
    return queue
