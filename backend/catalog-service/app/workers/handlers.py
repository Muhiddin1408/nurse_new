"""Event handlerlar. Har handler: (session, event) — biznes o'zgarishi va idempotentlik belgisi
BITTA tranzaksiyada (consumer o'zi ochadi). Commit/rollback qilmang.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart
(`scripts/check-contracts.py`).
"""

from __future__ import annotations

from medbron_common.eventbus import EventConsumer
from medbron_common.events import Events

from app.services import ratings


def register(consumer: EventConsumer) -> None:
    consumer.on(Events.RATING_RECALCULATED)(ratings.apply)
