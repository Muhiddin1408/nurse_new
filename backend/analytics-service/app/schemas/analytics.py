from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class Counts(BaseModel):
    created: int
    confirmed: int
    completed: int
    cancelled: int
    no_show: int
    expired: int
    gmv_tiyin: int
    captured_tiyin: int
    refunded_tiyin: int
    reversed_tiyin: int
    net_payments_tiyin: int


class SummaryOut(Counts):
    date_from: date
    date_to: date
    conversion_rate: float
    cancellation_rate: float
    no_show_rate: float


class DayOut(Counts):
    day: date


class FunnelOut(BaseModel):
    date_from: date
    date_to: date
    created: int
    needs_payment: int
    paid: int
    confirmed: int
    completed: int
    cancelled: int
    expired: int
    no_show: int
    conversion_rate: float


class TopDoctorOut(BaseModel):
    doctor_id: UUID
    completed: int
    gmv_tiyin: int
    no_show: int
    cancelled: int
