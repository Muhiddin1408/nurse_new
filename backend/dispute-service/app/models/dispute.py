"""dispute-service ma'lumotlari: nizo (holat mashinasi, SLA, yechim) va dalillar (file-service fayl ID'lari).

Bron ma'lumoti (raqam, shifokor, to'langan summa) — nizo ochilgan paytdagi booking `/summary` nusxasi.

    open ──admin resolve (refund > 0)──> resolved ──refund.succeeded──> closed
      └───admin resolve (rejected)─────────────────────────────────────> closed
SLA (`due_at`) o'tsa — `sla_breached_at` bir marta qo'yiladi va `dispute.sla_breached` (eskalatsiya).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

OPEN, RESOLVED, CLOSED = "open", "resolved", "closed"
STATUSES = (OPEN, RESOLVED, CLOSED)

REASONS = ("doctor_no_show", "poor_service", "wrong_charge", "other")
REFUND_FULL, REFUND_PARTIAL, REJECTED = "refund_full", "refund_partial", "rejected"
RESOLUTIONS = (REFUND_FULL, REFUND_PARTIAL, REJECTED)

# Nizo ochish mumkin bo'lgan bron holatlari (in_progress — monolitdagi confirmed + started)
DISPUTABLE_BOOKING_STATUSES = ("confirmed", "in_progress", "completed", "no_show")


class Dispute(Base):
    __tablename__ = "disputes"
    __table_args__ = (
        # Bitta bronga bitta nizo: admin qarori yakuniy (monolitda yopilgandan keyin qayta ochilib, qayta refund
        # so'rash mumkin edi)
        Index("uq_disputes_booking", "booking_id", unique=True),
        Index("ix_disputes_client_created", "client_id", "created_at"),
        Index("ix_disputes_status_due", "status", "due_at"),
        Index("ix_disputes_sla_pending", "due_at", postgresql_where=sql("status = 'open' AND sla_breached_at IS NULL")),
        CheckConstraint("refund_tiyin >= 0 AND refunded_tiyin >= 0", name="amounts_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    booking_number: Mapped[str] = mapped_column(String(20), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Nizo ochilgandagi to'langan summa (booking `paid_tiyin`) — refund chegarasi
    paid_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=OPEN)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(20))
    refund_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    doctor_liable: Mapped[bool | None] = mapped_column(Boolean)
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # payment `refund.succeeded` (source=dispute) — haqiqatda qaytarilgan summa (payment qoldiq bilan cheklaydi)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    refunded_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Evidence(Base):
    """Dalil — file-service'dagi fayl (`purpose=dispute_evidence`, private). Fayl o'zi shu yerda saqlanmaydi."""

    __tablename__ = "dispute_evidence"
    __table_args__ = (Index("uq_dispute_evidence_file", "dispute_id", "file_id", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dispute_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("disputes.id"), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    added_by: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
