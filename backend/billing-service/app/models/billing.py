"""billing-service ma'lumotlari: shifokor daromad daftari (earning_entries), payout davrlari va to'lov
rekvizitlari (ADR-0012).

Daftar yozuvi — shifokorning IMZOLI ulushi: qabul uchun `earning` (+), mijozga qaytarilgan pulning
shifokor ulushi `refund` / `reversal` (−). Payout — payout'ga kirmagan yozuvlar yig'indisi; payout'dan
keyin kelgan refund keyingi payout'dan ayiriladi (yozuv hali `payout_id` siz). Ulushlar bron
`money_split` snapshot'idan va payment eventidagi `doctor_share_tiyin` dan — billing QAYTA HISOBLAMAYDI."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

EARNING, REFUND, REVERSAL = "earning", "refund", "reversal"
PENDING, PAID = "pending", "paid"


class EarningEntry(Base):
    __tablename__ = "earning_entries"
    __table_args__ = (
        # Idempotentlik: bitta hodisa daftarga bir marta (earning — booking_id, refund — refund_id,
        # reversal — payment_id)
        UniqueConstraint("kind", "source_ref", name="uq_earning_entries_kind_source"),
        Index("ix_earning_entries_unpaid", "doctor_id", "eligible_at", postgresql_where=sql("payout_id IS NULL")),
        Index("ix_earning_entries_booking", "booking_id"),
        Index("ix_earning_entries_doctor_start", "doctor_id", "start_at"),
        Index(
            "ix_earning_entries_clinic_start", "clinic_id", "start_at", postgresql_where=sql("clinic_id IS NOT NULL")
        ),
        Index("ix_earning_entries_payout", "payout_id", postgresql_where=sql("payout_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Refund earning'dan oldin kelsa — NULL; earning kelganda bron bo'yicha to'ldiriladi
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    booking_number: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    booking_status: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    services: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    # Shifokor ulushi (imzoli) — payout shu ustun yig'indisi
    amount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Hisobot uchun: summa va uning platforma / provayder qismlari (refundda manfiy)
    gross_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payout_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("payouts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Payout(Base):
    __tablename__ = "payouts"
    __table_args__ = (
        UniqueConstraint("doctor_id", "period_start", "period_end", name="uq_payouts_doctor_period"),
        Index("ix_payouts_status_period", "status", "period_end"),
        Index("ix_payouts_doctor", "doctor_id", "period_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=PENDING, server_default=PENDING)
    bookings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    platform_fee_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    provider_fee_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Refund/reversal'larning shifokor ulushi (musbat): net = gross − platform − provider − refunds
    refunds_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    net_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    bank_reference: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PayoutAccount(Base):
    """D6: shifokor to'lov rekvizitlari. To'liq raqam API javobida HECH QACHON qaytmaydi."""

    __tablename__ = "payout_accounts"

    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    holder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    account_number: Mapped[str] = mapped_column(String(34), nullable=False)
    mfo: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="")
    inn: Mapped[str] = mapped_column(String(14), nullable=False, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def masked_number(self) -> str:
        return "•••• " + self.account_number[-4:]

    def as_dict(self) -> dict[str, Any]:
        return {
            "holder_name": self.holder_name,
            "bank_name": self.bank_name,
            "masked_number": self.masked_number,
            "mfo": self.mfo,
            "inn": self.inn,
        }
