"""RabbitMQ topologiyasini bir marta, idempotent e'lon qiladi (`dlx-bootstrap` konteyneri).

    medbron.events (topic) · medbron.dlx (topic) → medbron.dlq · medbron.retry (headers) → retry.{5s,30s,5m}

Topologiya kodi `libs/py-common` da (servislar ham aynan shu funksiyani chaqiradi) — ikki xil
e'lon bo'lmasligi uchun bu skript faqat uni chaqiradi.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import aio_pika

from medbron_common.eventbus.topology import MEDBRON_TOPOLOGY, declare_topology
from medbron_common.logging import configure_logging

logger = logging.getLogger("dlx-bootstrap")
ATTEMPTS = 30


async def main() -> int:
    configure_logging("dlx-bootstrap", os.environ.get("LOG_LEVEL", "INFO"))
    url = os.environ["RABBITMQ_URL"]
    for attempt in range(1, ATTEMPTS + 1):
        try:
            connection = await aio_pika.connect(url)
            break
        except Exception as exc:  # broker hali ko'tarilmagan
            logger.warning("broker not ready", extra={"attempt": attempt, "error": repr(exc)})
            await asyncio.sleep(2)
    else:
        logger.error("broker unavailable, giving up")
        return 1
    async with connection:
        channel = await connection.channel()
        await declare_topology(channel, MEDBRON_TOPOLOGY)
    t = MEDBRON_TOPOLOGY
    logger.info(
        "topology declared",
        extra={"exchange": t.exchange, "dlx": t.dlx, "dlq": t.dlq, "retry": [n for n, _ in t.retry_tiers]},
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
