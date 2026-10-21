"""DLQ ni ko'rish va qayta yuborish (runbook: docs/runbooks/dlq.md).

    python -m medbron_common.eventbus.dlq list
    python -m medbron_common.eventbus.dlq replay --queue booking-service.events \
        [--event-type payment.captured] [--limit 100]
    python -m medbron_common.eventbus.dlq replay --queue booking-service.events --dry-run

Xabar `x-source-queue` dagi AYNAN o'sha consumer navbatiga (default exchange orqali) qaytariladi —
asosiy exchange'ga emas, aks holda boshqa servislar uni qayta olardi. `x-retry-count` nolga
tushiriladi; `x-dlq-replayed` hisoblagichi oshadi. Tanlanmagan xabarlar DLQ da qoladi.
Consumer idempotent (`processed_events`), shuning uchun qayta yuborish xavfsiz.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika.abc import AbstractIncomingMessage

from medbron_common.eventbus.consumer import RETRY_COUNT_HEADER
from medbron_common.eventbus.topology import MEDBRON_TOPOLOGY, RETRY_TIER_HEADER, Topology


@dataclass(frozen=True)
class DeadLetter:
    message_id: str | None
    event_type: str | None
    source_queue: str | None
    reason: str | None
    error: str | None


def _describe(message: AbstractIncomingMessage) -> DeadLetter:
    headers = message.headers or {}

    def text(key: str) -> str | None:
        value = headers.get(key)
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return None if value is None else str(value)

    return DeadLetter(
        message_id=message.message_id,
        event_type=message.type,
        source_queue=text("x-source-queue"),
        reason=text("x-dead-letter-reason"),
        error=text("x-last-error"),
    )


async def _drain(channel: aio_pika.abc.AbstractChannel, topology: Topology) -> list[AbstractIncomingMessage]:
    """DLQ dagi barcha xabarlarni ack QILMASDAN oladi (kanal yopilsa qaytib boradi)."""
    queue = await channel.declare_queue(topology.dlq, passive=True)
    messages: list[AbstractIncomingMessage] = []
    while (message := await queue.get(no_ack=False, fail=False)) is not None:
        messages.append(message)
    return messages


async def list_dead_letters(url: str, topology: Topology = MEDBRON_TOPOLOGY) -> list[DeadLetter]:
    connection = await aio_pika.connect(url)
    async with connection:
        channel = await connection.channel()
        messages = await _drain(channel, topology)
        described = [_describe(m) for m in messages]
        for m in messages:
            await m.nack(requeue=True)
    return described


async def replay(
    url: str,
    *,
    queue: str,
    event_type: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    topology: Topology = MEDBRON_TOPOLOGY,
) -> int:
    """Mos xabarlarni manba navbatiga qaytaradi. Qaytaradi: qayta yuborilganlar soni."""
    connection = await aio_pika.connect(url)
    replayed = 0
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        messages = await _drain(channel, topology)
        for message in messages:
            info = _describe(message)
            selected = (
                info.source_queue == queue
                and (event_type is None or info.event_type == event_type)
                and (limit is None or replayed < limit)
            )
            if not selected or dry_run:
                await message.nack(requeue=True)
                replayed += int(selected)
                continue
            headers: dict[str, Any] = dict(message.headers or {})
            for key in (RETRY_COUNT_HEADER, RETRY_TIER_HEADER, "x-dead-letter-reason", "x-last-error", "x-death"):
                headers.pop(key, None)
            headers["x-dlq-replayed"] = int(str(headers.get("x-dlq-replayed", 0) or 0)) + 1
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=message.body,
                    headers=headers,
                    content_type=message.content_type,
                    message_id=message.message_id,
                    type=message.type,
                    timestamp=message.timestamp,
                    app_id=message.app_id,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=queue,
            )
            await message.ack()
            replayed += 1
    return replayed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m medbron_common.eventbus.dlq")
    parser.add_argument("--url", default=os.environ.get("RABBITMQ_URL"), help="standart: $RABBITMQ_URL")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="DLQ dagi xabarlar (navbat va sabab bo'yicha)")
    rp = sub.add_parser("replay", help="xabarlarni manba navbatiga qaytarish")
    rp.add_argument("--queue", required=True, help="manba navbat, masalan booking-service.events")
    rp.add_argument("--event-type")
    rp.add_argument("--limit", type=int)
    rp.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("--url yoki RABBITMQ_URL kerak")

    if args.command == "list":
        letters = asyncio.run(list_dead_letters(args.url))
        summary = Counter((d.source_queue, d.event_type, d.reason) for d in letters)
        print(
            json.dumps(
                [{"queue": q, "event_type": e, "reason": r, "count": n} for (q, e, r), n in summary.most_common()],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    count = asyncio.run(
        replay(args.url, queue=args.queue, event_type=args.event_type, limit=args.limit, dry_run=args.dry_run)
    )
    print(f"{'tanlanadi' if args.dry_run else 'qayta yuborildi'}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
