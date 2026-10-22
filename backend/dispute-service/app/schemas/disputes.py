from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Reason = Literal["doctor_no_show", "poor_service", "wrong_charge", "other"]


class OpenIn(BaseModel):
    booking_id: UUID
    reason: Reason
    description: str = Field("", max_length=2000)


class EvidenceIn(BaseModel):
    file_id: UUID = Field(description="file-service'ga `purpose=dispute_evidence` bilan yuklangan va yakunlangan fayl")


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: UUID
    content_type: str
    created_at: datetime
    url: str | None = None  # faqat admin ko'rinishida (qisqa muddatli presigned)


class DisputeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_id: UUID
    booking_number: str
    reason: str
    description: str
    status: str
    due_at: datetime
    resolution: str | None
    refund_tiyin: int
    refunded_tiyin: int
    resolution_note: str
    resolved_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    evidence: list[EvidenceOut] = []


class AdminDisputeOut(DisputeOut):
    client_id: UUID
    doctor_id: UUID
    paid_tiyin: int
    doctor_liable: bool | None
    resolved_by: UUID | None
    sla_breached_at: datetime | None
    refund_id: UUID | None


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["refund_full", "refund_partial", "rejected"]
    refund_tiyin: int = Field(0, ge=0, description="refund_partial uchun majburiy; refund_full — to'langan summa")
    doctor_liable: bool = Field(False, description="true — refund shifokor ulushidan ham ushlanadi (billing)")
    note: str = Field(min_length=1, max_length=2000)
