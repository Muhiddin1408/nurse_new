"""Transactional Outbox (BLUEPRINT 8.4).

Servis:
    class OutboxEvent(OutboxMixin, Base): pass
    class ProcessedEvent(ProcessedEventMixin, Base): pass
    outbox = Outbox(OutboxEvent, producer="booking-service")

    async with session.begin():
        session.add(booking)
        outbox.add_event(session, Events.BOOKING_CREATED, aggregate_id=booking.id, data={...})
    # biznes yozuvi va event — BITTA tranzaksiyada. "DB'ga yozildi, event yo'qoldi" bo'lmaydi.

Relay (alohida konteyner):
    python -m app.workers.outbox_relay   →   OutboxRelay(...).run(stop)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from medbron_common.eventbus.envelope import build_envelope
from medbron_common.events import CATALOG, validate_event
from medbron_common.metrics import OUTBOX_FAILED, OUTBOX_PENDING, OUTBOX_PUBLISH_ERRORS, OUTBOX_PUBLISHED
from medbron_common.telemetry import current_trace_id, current_traceparent

logger = logging.getLogger(__name__)

PENDING, PUBLISHED, FAILED = "pending", "published", "failed"


class OutboxMixin:
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    traceparent: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=PENDING, server_default=PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        return (
            # Relay so'rovi faqat shu qisman indeksga tayanadi — published qatorlar uni shishirmaydi.
            Index("ix_outbox_events_pending", "next_attempt_at", postgresql_where=text("status = 'pending'")),
            Index("ix_outbox_events_published_at", "published_at", postgresql_where=text("status = 'published'")),
            CheckConstraint("status IN ('pending', 'published', 'failed')", name="status"),
        )


class ProcessedEventMixin:
    """Idempotent consumer (BLUEPRINT 8.5): `processed_events(event_id PK, event_type, processed_at)`."""

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class UnknownEvent(ValueError):
    pass


class NotAPublisher(ValueError):
    pass


class Outbox:
    def __init__(self, model: type[OutboxMixin], *, producer: str, validate: bool = True) -> None:
        self.model = model
        self.producer = producer
        self.validate = validate

    def add_event(
        self,
        session: AsyncSession,
        event_type: str,
        *,
        aggregate_id: str | uuid.UUID,
        data: dict[str, Any],
        aggregate_type: str | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Eventni joriy tranzaksiyaga qo'shadi. Commit — chaqiruvchining ishi."""
        spec = CATALOG.get(event_type)
        if spec is None:
            raise UnknownEvent(f"{event_type!r} contracts/events/catalog.yaml da yo'q")
        if self.producer not in spec.publishers:
            raise NotAPublisher(f"{self.producer} {event_type} ni publish qila olmaydi (katalog: {spec.publishers})")
        envelope = build_envelope(
            event_type=event_type,
            event_version=spec.version,
            producer=self.producer,
            aggregate_type=aggregate_type or spec.aggregate,
            aggregate_id=aggregate_id,
            data=data,
            trace_id=current_trace_id(),
            occurred_at=occurred_at,
        )
        if self.validate:
            # Kontraktni buzgan event brokerga emas, shu yerda — commit'dan OLDIN — yiqiladi.
            validate_event(envelope)
        factory: Callable[..., OutboxMixin] = self.model
        session.add(
            factory(
                event_id=uuid.UUID(envelope["event_id"]),
                aggregate_type=envelope["aggregate_type"],
                aggregate_id=envelope["aggregate_id"],
                event_type=event_type,
                event_version=spec.version,
                payload=envelope,
                traceparent=current_traceparent(),
                status=PENDING,
                attempts=0,
                next_attempt_at=datetime.now(UTC),
            )
        )
        return envelope


class Publisher(Protocol):
    async def publish(self, envelope: dict[str, Any], *, traceparent: str | None = None) -> None: ...


class OutboxRelay:
    """Relay algoritmi (BLUEPRINT 8.4):

    1. `pending AND next_attempt_at <= now()` → `FOR UPDATE SKIP LOCKED` (bir nechta relay xavfsiz)
    2. publish (timeout bilan) → `published`
    3. xato → `attempts++`, `next_attempt_at = now + min(base*2^(n-1), max)`; `attempts >= MAX` → `failed` + alert
    4. batch to'la bo'lsa darhol takrorlash, aks holda POLL_INTERVAL kutish
    5. stop event (SIGTERM) → joriy batch tugagach toza chiqish
    6. eski `published` qatorlarni tozalash
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        model: type[OutboxMixin],
        publisher: Publisher,
        *,
        batch_size: int = 100,
        poll_interval: float = 1.0,
        publish_timeout: float = 5.0,
        max_attempts: int = 10,
        backoff_base: float = 1.0,
        backoff_max: float = 300.0,
        retention: timedelta = timedelta(days=7),
        purge_interval: float = 3600.0,
        gauge_interval: float = 10.0,
    ) -> None:
        self.session_factory = session_factory
        self.model = model
        self.publisher = publisher
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.publish_timeout = publish_timeout
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.retention = retention
        self.purge_interval = purge_interval
        self.gauge_interval = gauge_interval

    def backoff(self, attempts: int) -> timedelta:
        return timedelta(seconds=min(self.backoff_base * 2 ** (attempts - 1), self.backoff_max))

    async def run_once(self) -> int:
        """Bitta batch. Qaytaradi: qayta ishlangan qatorlar soni."""
        m = self.model
        async with self.session_factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(m)
                    .where(m.status == PENDING, m.next_attempt_at <= func.now())
                    .order_by(m.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                await self._publish_row(row)
        return len(rows)

    async def _publish_row(self, row: OutboxMixin) -> None:
        now = datetime.now(UTC)
        try:
            await asyncio.wait_for(
                self.publisher.publish(row.payload, traceparent=row.traceparent), timeout=self.publish_timeout
            )
        except Exception as exc:
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            OUTBOX_PUBLISH_ERRORS.labels(row.event_type).inc()
            if row.attempts >= self.max_attempts:
                row.status = FAILED
                OUTBOX_FAILED.labels(row.event_type).inc()
                # ALERT: outbox `failed` > 0 — qo'lda tekshiriladi va qayta `pending` qilinadi.
                logger.error(
                    "outbox event failed permanently",
                    extra={"event_id": str(row.event_id), "event_type": row.event_type, "error": row.last_error},
                )
            else:
                row.next_attempt_at = now + self.backoff(row.attempts)
                logger.warning(
                    "outbox publish failed, will retry",
                    extra={"event_id": str(row.event_id), "attempts": row.attempts, "error": row.last_error},
                )
        else:
            row.status = PUBLISHED
            row.published_at = now
            row.last_error = None
            OUTBOX_PUBLISHED.labels(row.event_type).inc()

    async def purge(self) -> int:
        m = self.model
        cutoff = datetime.now(UTC) - self.retention
        async with self.session_factory() as session, session.begin():
            result = await session.execute(delete(m).where(m.status == PUBLISHED, m.published_at < cutoff))
        deleted = int(getattr(result, "rowcount", 0) or 0)
        if deleted:
            logger.info("outbox purged", extra={"deleted": deleted})
        return deleted

    async def pending_count(self) -> int:
        m = self.model
        async with self.session_factory() as session:
            count = await session.scalar(select(func.count()).select_from(m).where(m.status == PENDING))
        return int(count or 0)

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("outbox relay started", extra={"batch_size": self.batch_size})
        loop = asyncio.get_running_loop()
        next_purge = loop.time()
        next_gauge = loop.time()
        while not stop.is_set():
            try:
                processed = await self.run_once()
                now = loop.time()
                if now >= next_gauge:
                    OUTBOX_PENDING.set(await self.pending_count())
                    next_gauge = now + self.gauge_interval
                if now >= next_purge:
                    await self.purge()
                    next_purge = now + self.purge_interval
            except Exception:
                logger.exception("outbox relay iteration failed")
                processed = 0
            if processed < self.batch_size:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
        logger.info("outbox relay stopping")
