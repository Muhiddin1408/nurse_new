"""payment-service ma'lumotlari: to'lov urinishlari (provayder holat mashinasi), refundlar, ikki yozuvli daftar,
reconciliation farqlari, provayder so'rov/javob jurnali. Pul — tiyin (ADR-0007).

Bron ma'lumotlari (summa, money_split, fiskal elementlar) — checkout paytida booking `/payable` dan olingan
SNAPSHOT: webhook kelganda booking'ga qayta murojaat qilinmaydi va chek aynan to'lov summasidan chiqadi."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

PAYME, CLICK, CASH = "payme", "click", "cash"
ONLINE_PROVIDERS = (PAYME, CLICK)

# Payment holatlari (ARCHITECTURE 7.4)
CREATED, PROCESSING, SUCCEEDED, FAILED = "created", "processing", "succeeded", "failed"
CANCELLED, EXPIRED = "cancelled", "expired"
PARTIALLY_REFUNDED, REFUNDED, REVERSED = "partially_refunded", "refunded", "reversed"
OPEN_STATUSES = (CREATED, PROCESSING)
# Pul yechilgan (keyin qaytarilgan bo'lsa ham) — bitta bronda faqat BITTA shunday to'lov
MONEY_TAKEN_STATUSES = (SUCCEEDED, PARTIALLY_REFUNDED, REFUNDED, REVERSED)
REFUNDABLE_STATUSES = (SUCCEEDED, PARTIALLY_REFUNDED)

# Refund holatlari va manbalari (refund.succeeded.source)
R_PENDING, R_SUCCEEDED, R_FAILED, R_MANUAL = "pending", "succeeded", "failed", "manual_required"
REFUND_SOURCES = ("booking_cancelled", "booking_no_show", "confirmation_failed", "dispute", "admin")

# Daftar hisoblari
CLIENT_PAYMENTS = "client_payments"  # aktiv: provayderda / bankda turgan pul
PLATFORM_REVENUE = "platform_revenue"
DOCTOR_PAYABLE = "doctor_payable"
PROVIDER_FEES = "provider_fees"
ACCOUNTS = (CLIENT_PAYMENTS, PLATFORM_REVENUE, DOCTOR_PAYABLE, PROVIDER_FEES)


class Payment(Base):
    """Bitta bronga bir nechta urinish (Payme bekor bo'ldi → Click), lekin pul faqat BIR marta yechiladi.

    created ──Create/Prepare──> processing ──Perform/Complete──> succeeded ──refund──> (partially_)refunded
       │                           ├──Cancel(-1) / Click error──> cancelled         └──Cancel(-2)──> reversed
       └──12 soat──> expired       └──12 soat──> expired
    """

    __tablename__ = "payments"
    __table_args__ = (
        Index(
            "uq_payments_money_taken_per_booking",
            "booking_id",
            unique=True,
            postgresql_where=sql("status IN ('succeeded', 'partially_refunded', 'refunded', 'reversed')"),
        ),
        Index(
            "uq_payments_provider_external",
            "provider",
            "external_id",
            unique=True,
            postgresql_where=sql("external_id <> ''"),
        ),
        Index("ix_payments_booking_status", "booking_id", "status"),
        Index("ix_payments_provider_create_time", "provider", "provider_create_time"),
        Index("ix_payments_open_created", "created_at", postgresql_where=sql("status IN ('created', 'processing')")),
        Index(
            "ix_payments_fiscal_pending",
            "updated_at",
            postgresql_where=sql("provider = 'click' AND status = 'succeeded' AND fiscal_submitted_at IS NULL"),
        ),
        CheckConstraint("amount_tiyin > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    booking_number: Mapped[str] = mapped_column(String(20), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    amount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CREATED)
    # Provayderdagi tranzaksiya ID (Payme `id`, Click `click_trans_id`)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    # Protokol vaqtlari (ms). CheckTransaction/GetStatement ularni AYNAN birinchi javobdagidek qaytaradi;
    # Click `merchant_prepare_id` / `merchant_confirm_id` ham shulardan.
    provider_create_time: Mapped[int | None] = mapped_column(BigInteger)
    perform_time: Mapped[int | None] = mapped_column(BigInteger)
    cancel_time: Mapped[int | None] = mapped_column(BigInteger)
    cancel_reason: Mapped[int | None] = mapped_column(SmallInteger)
    provider_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # Bron snapshot'i (booking /payable): money_split va fiskal chek elementlari
    money: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    receipt_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    # B3: pul yechildi, bron tasdiqlanmadi (booking.confirmation_failed) — avtomatik refund
    needs_refund: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fiscal_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Refund(Base):
    """Pulni qaytarish. Bitta to'lovga bir nechta qisman refund; sum(refunds) ≤ payment.amount (qator qulfi bilan).

    pending ──provider.refund()──> succeeded
       ├── vaqtinchalik xato → retry (2^n daq + jitter), `refund_max_attempts` dan keyin failed + refund.failed
       └── provayder API orqali mumkin emas (Payme, Click qisman) → manual_required (kabinetda, keyin admin belgilaydi)
    """

    __tablename__ = "refunds"
    __table_args__ = (
        # Idempotentlik: bitta manba hodisasi uchun bitta refund (takroriy event / takroriy so'rov)
        Index("uq_refunds_source", "source", "source_ref", unique=True),
        Index("ix_refunds_due", "next_attempt_at", postgresql_where=sql("status = 'pending'")),
        Index("ix_refunds_booking", "booking_id"),
        CheckConstraint("amount_tiyin > 0", name="amount_positive"),
        CheckConstraint("doctor_share_tiyin >= 0", name="doctor_share_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("payments.id"), nullable=False)
    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    amount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Shifokor ulushi (ADR-0012): dispute doctor_liable=false bo'lsa 0 — hammasini platforma ko'taradi
    doctor_share_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    initiated_by: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=R_PENDING)
    external_refund_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LedgerTransaction(Base):
    """Daftarning bitta amali: ichidagi yozuvlar sum(debit) == sum(credit). Faqat INSERT — xato teskari amal bilan."""

    __tablename__ = "ledger_transactions"
    __table_args__ = (
        # Idempotentlik: bitta hodisa daftarga ikki marta tushmaydi
        Index("uq_ledger_kind_ref", "kind", "ref_id", unique=True),
        Index("ix_ledger_booking", "booking_id", postgresql_where=sql("booking_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # payment_captured | refund_succeeded | ...
    ref_type: Mapped[str] = mapped_column(String(20), nullable=False)  # payment | refund | payout
    ref_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "debit_tiyin >= 0 AND credit_tiyin >= 0 AND (debit_tiyin = 0 OR credit_tiyin = 0)",
            name="one_side_non_negative",
        ),
        Index("ix_ledger_entries_account_doctor", "account", "doctor_id"),
        Index("ix_ledger_entries_transaction", "transaction_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ledger_transactions.id"), nullable=False
    )
    account: Mapped[str] = mapped_column(String(30), nullable=False)
    debit_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    credit_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Discrepancy(Base):
    """Reconciliation topgan farq. Maqsad — doimiy 0; har biri admin tomonidan hal qilinadi."""

    __tablename__ = "discrepancies"
    __table_args__ = (
        # Qayta ishga tushirilganda dublikat yo'q. NULL'li ustunlar UNIQUE'da teng emas — shuning uchun fingerprint.
        Index("uq_discrepancies_fingerprint_day", "fingerprint", "day", unique=True),
        Index("ix_discrepancies_status_type", "status", "type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="")
    external_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    payment_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    booking_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    local_tiyin: Mapped[int | None] = mapped_column(BigInteger)
    provider_tiyin: Mapped[int | None] = mapped_column(BigInteger)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    day: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open", server_default="open")
    resolution_note: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProviderLog(Base):
    """Provayder bilan HAR bir almashinuv (kiruvchi webhook va chiquvchi so'rov). Nizoda yagona dalil.
    Faqat INSERT; imzo/parol yozilmaydi. Retention — `provider_log_retention_days`."""

    __tablename__ = "provider_logs"
    __table_args__ = (Index("ix_provider_logs_provider_op_created", "provider", "operation", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)  # in | out
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ip: Mapped[str | None] = mapped_column(INET)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
