"""Outbox (BLUEPRINT 8.4) va idempotent consumer (8.5) jadvallari — sxema libs'da, bir xil."""

from medbron_common.eventbus import OutboxMixin, ProcessedEventMixin

from app.core.database import Base


class OutboxEvent(OutboxMixin, Base):
    pass


class ProcessedEvent(ProcessedEventMixin, Base):
    pass
