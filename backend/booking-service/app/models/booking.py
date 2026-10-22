"""booking-service ma'lumotlari: bron (holat mashinasi, pul snapshot'i), xizmat qatorlari, idempotency
kalitlari, waitlist. Narx — pricing quote'idan muzlatilgan nusxa (keyin catalog/pricing o'zgarsa ham bron
o'zgarmaydi). Bemor/manzil — bron paytidagi nusxa (user-service'da o'chirilsa ham tarix buzilmaydi)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

PENDING_PAYMENT, CONFIRMED, IN_PROGRESS = "pending_payment", "confirmed", "in_progress"
COMPLETED, CANCELLED, EXPIRED, NO_SHOW = "completed", "cancelled", "expired", "no_show"
LIVE_STATUSES = (PENDING_PAYMENT, CONFIRMED, IN_PROGRESS)
FINAL_STATUSES = (COMPLETED, CANCELLED, EXPIRED, NO_SHOW)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        # Bitta slotda bitta jonli bron (monolit `uniq_active_booking_per_slot`)
        Index(
            "uq_bookings_live_slot",
            "slot_id",
            unique=True,
            postgresql_where=sql("status IN ('pending_payment', 'confirmed', 'in_progress')"),
        ),
        Index("ix_bookings_client_created", "client_id", "created_at"),
        Index("ix_bookings_doctor_start", "doctor_id", "start_at"),
        Index("ix_bookings_clinic_start", "clinic_id", "start_at", postgresql_where=sql("clinic_id IS NOT NULL")),
        Index(
            "ix_bookings_patient_live",
            "patient_id",
            postgresql_where=sql("status IN ('pending_payment', 'confirmed', 'in_progress')"),
        ),
        Index("ix_bookings_due", "payment_due_at", postgresql_where=sql("status = 'pending_payment'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    slot_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    address_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    place: Mapped[str] = mapped_column(String(8), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # --- pul (tiyin) — pricing quote snapshot'i -------------------------------------------
    quote_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    payment_mode: Mapped[str] = mapped_column(String(10), nullable=False)
    payment_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_adjust_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    discount_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    discount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    discount_borne_by: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="")
    promo_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    total_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prepay_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    commission_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_fee_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    platform_fee_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_fee_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    doctor_payout_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # payment.captured / refund.succeeded dan
    paid_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    refunded_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    payment_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    # --- snapshot'lar ---------------------------------------------------------------------
    patient: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    client_comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")

    # --- hayot sikli ----------------------------------------------------------------------
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[str] = mapped_column(String(12), nullable=False, default="", server_default="")
    no_show_by: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[str] = mapped_column(String(12), nullable=False, default="", server_default="")
    cancel_reason_code: Mapped[str] = mapped_column(String(40), nullable=False, default="", server_default="")
    cancel_reason_text: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    rescheduled_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BookingItem(Base):
    __tablename__ = "booking_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_price_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mxik_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    package_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    vat_percent: Mapped[int | None] = mapped_column(SmallInteger)


class IdempotencyKey(Base):
    """`(client_id, key)` — bron sagasidan OLDIN yoziladi (ADR-0011 #1): parallel ikki so'rovdan bittasi
    UNIQUE'da to'xtaydi. `booking_id` NULL — saga davom etmoqda (yoki yiqildi va qator o'chirildi)."""

    __tablename__ = "idempotency_keys"

    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WaitlistEntry(Base):
    """Bo'shagan vaqt haqida xabar kutish (monolit C10): band qilish YO'Q — xabar beriladi, mijoz oddiy
    yo'l bilan bron qiladi (yaratilganda `converted`)."""

    __tablename__ = "waitlist_entries"
    __table_args__ = (
        Index(
            "uq_waitlist_live",
            "client_id",
            "doctor_id",
            unique=True,
            postgresql_where=sql("status IN ('active', 'notified')"),
        ),
        Index("ix_waitlist_doctor_status", "doctor_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    place: Mapped[str] = mapped_column(String(8), nullable=False)
    # active | notified | converted | cancelled | expired
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active", server_default="active")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
