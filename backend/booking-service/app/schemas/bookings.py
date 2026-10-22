from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateBookingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    doctor_id: UUID
    slot_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=10)
    address_id: UUID | None = None
    comment: str = Field(default="", max_length=1000)
    promo_code: str = Field(default="", max_length=32)


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_id: UUID
    service_name: str
    price_tiyin: int
    duration_minutes: int


class BookingOut(BaseModel):
    id: UUID
    number: str
    status: str
    place: str
    start_at: datetime
    end_at: datetime
    doctor_id: UUID
    clinic_id: UUID | None
    patient: dict[str, Any]
    address: dict[str, Any] | None
    items: list[ItemOut]
    original_tiyin: int
    price_adjust_percent: int
    discount_kind: str
    discount_tiyin: int
    promo_code: str
    total_tiyin: int
    prepay_tiyin: int
    payment_mode: str
    payment_due_at: datetime | None
    paid_tiyin: int
    rescheduled_count: int
    client_comment: str
    cancelled_by: str
    cancel_reason_code: str
    created_at: datetime


class BookingPage(BaseModel):
    results: list[BookingOut]
    next_cursor: str | None


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=500)


class DoctorCancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class CancellationPreviewOut(BaseModel):
    allowed: bool
    refund_tiyin: int
    penalty_tiyin: int
    reason_code: str


class RescheduleIn(BaseModel):
    new_slot_id: UUID


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=4000)


class AdminNoShowIn(BaseModel):
    absent: Literal["client", "doctor"]
    note: str = Field(default="", max_length=2000)


class AdminCancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class AppointmentOut(BaseModel):
    """Shifokor ko'rinishi. Batafsil manzil (kirish, qavat, xonadon) — faqat qabul kuni (monolit maxfiylik)."""

    id: UUID
    number: str
    status: str
    place: str
    # Klinika ko'rinishida qaysi shifokor (ism — catalog'dan, frontend keshi)
    doctor_id: UUID
    start_at: datetime
    end_at: datetime
    clinic_id: UUID | None
    patient: dict[str, Any]
    services: list[ItemOut]
    client_comment: str
    address: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
    note: str
    total_tiyin: int
    to_collect_tiyin: int


class AppointmentDetail(AppointmentOut):
    patient_history: list[dict[str, Any]]


class WaitlistIn(BaseModel):
    doctor_id: UUID
    date_from: date
    date_to: date
    place: Literal["clinic", "home"] = "clinic"


class WaitlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doctor_id: UUID
    date_from: date
    date_to: date
    place: str
    status: str
    notified_at: datetime | None
    created_at: datetime


class RebookOut(BaseModel):
    doctor_id: UUID
    doctor_bookable: bool
    place: str
    clinic_id: UUID | None
    patient_id: UUID
    address_id: UUID | None
    service_ids: list[UUID]
    slots: list[dict[str, Any]]
