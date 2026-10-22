from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CheckoutIn(BaseModel):
    booking_id: UUID
    provider: Literal["payme", "click"]


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_id: UUID
    booking_number: str
    provider: str
    status: str
    amount_tiyin: int
    created_at: datetime
    captured_at: datetime | None


class CheckoutOut(BaseModel):
    payment: PaymentOut
    checkout_url: str
    expires_at: datetime | None


class RefundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_id: UUID
    booking_id: UUID
    amount_tiyin: int
    doctor_share_tiyin: int
    source: str
    source_ref: str
    reason: str
    initiated_by: str
    status: str
    external_refund_id: str
    attempts: int
    next_attempt_at: datetime | None
    last_error: str
    completed_at: datetime | None
    created_at: datetime


class AdminPaymentOut(PaymentOut):
    client_id: UUID
    doctor_id: UUID
    clinic_id: UUID | None
    external_id: str
    needs_refund: bool
    provider_create_time: int | None
    perform_time: int | None
    cancel_time: int | None
    cancel_reason: int | None
    money: dict[str, Any]
    fiscal_submitted_at: datetime | None
    updated_at: datetime


class AdminPaymentDetail(AdminPaymentOut):
    receipt_items: list[dict[str, Any]]
    provider_state: dict[str, Any]
    refunds: list[RefundOut] = []


class RefundIn(BaseModel):
    payment_id: UUID
    amount_tiyin: int = Field(gt=0)
    reason: str = Field("", max_length=64)


class CompleteRefundIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=64, description="Kabinetdagi qaytarish hujjati / tranzaksiya")
    note: str = Field("", max_length=2000)


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account: str
    debit_tiyin: int
    credit_tiyin: int
    doctor_id: UUID | None


class LedgerTransactionOut(BaseModel):
    id: UUID
    kind: str
    ref_type: str
    ref_id: UUID
    booking_id: UUID | None
    description: str
    created_at: datetime
    entries: list[LedgerEntryOut]


class BalancesOut(BaseModel):
    doctor_id: UUID | None
    balances: dict[str, int]
    trial_balance: int = Field(description="sum(debit) − sum(credit); har doim 0 bo'lishi shart")


class DiscrepancyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    provider: str
    external_id: str
    payment_id: UUID | None
    booking_id: UUID | None
    local_tiyin: int | None
    provider_tiyin: int | None
    details: dict[str, Any]
    day: date
    status: str
    resolution_note: str
    created_at: datetime


class ResolveIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)
