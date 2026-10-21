from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PayoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doctor_id: UUID
    period_start: date
    period_end: date
    status: str
    bookings_count: int
    gross_tiyin: int
    platform_fee_tiyin: int
    provider_fee_tiyin: int
    refunds_tiyin: int
    net_tiyin: int
    paid_at: datetime | None
    bank_reference: str


class PayoutAccountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holder_name: str = Field(min_length=1, max_length=255)
    bank_name: str = Field(default="", max_length=255)
    # Karta (16) yoki hisob raqami (20)
    account_number: str = Field(pattern=r"^(\d{16}|\d{20})$")
    mfo: str = Field(default="", pattern=r"^(\d{5})?$")
    inn: str = Field(default="", pattern=r"^(\d{9}(\d{5})?)?$")


class PayoutAccountOut(BaseModel):
    holder_name: str
    bank_name: str
    masked_number: str
    mfo: str
    inn: str


class EarningLineOut(BaseModel):
    booking_id: UUID
    doctor_id: UUID | None
    number: str
    date: date | None
    services: list[str]
    status: str
    gross_tiyin: int
    platform_fee_tiyin: int
    provider_fee_tiyin: int
    refunds_tiyin: int
    net_tiyin: int
    payout_id: UUID | None
    payout_status: str | None


class EarningsOut(BaseModel):
    period_from: date
    period_to: date
    bookings_count: int
    gross_tiyin: int
    platform_fee_tiyin: int
    provider_fee_tiyin: int
    refunds_tiyin: int
    net_tiyin: int
    paid_out_tiyin: int
    awaiting_payout_tiyin: int
    transactions: list[EarningLineOut]


class BuildIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Davrning istalgan kuni (yakshanbaga yaxlitlanadi); bo'sh — o'tgan to'liq hafta
    period_end: date | None = None


class MarkPaidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bank_reference: str = Field(min_length=1, max_length=255, pattern=r"\S")
