from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Place = Literal["clinic", "home"]


class InternalQuoteRequest(BaseModel):
    client_id: UUID
    doctor_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=10)
    slot_start_at: datetime
    price_adjust_percent: int = Field(ge=-90, le=100)
    place: Place
    promo_code: str = Field(default="", max_length=32)


class CommitRequest(BaseModel):
    booking_id: UUID
    client_id: UUID


class PreviewRequest(BaseModel):
    """Ilova narxni oldindan ko'rsatadi. `price_adjust_percent` — slot ro'yxatidagi qiymat (bo'lmasa 0);
    yakuniy narxni booking schedule'dagi haqiqiy foiz bilan hisoblaydi."""

    doctor_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=10)
    promo_code: str = Field(default="", max_length=32)
    price_adjust_percent: int = Field(default=0, ge=-90, le=100)
    place: Place | None = None


class PreviewOut(BaseModel):
    original_tiyin: int
    price_adjust_percent: int
    discount_kind: str
    discount_tiyin: int
    promo_code: str
    total_tiyin: int
    payment_mode: str
    prepay_tiyin: int
    items: list[dict[str, Any]]


class PromoValidateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    doctor_id: UUID
    service_ids: list[UUID] = Field(min_length=1, max_length=10)
    price_adjust_percent: int = Field(default=0, ge=-90, le=100)


class PromoValidateOut(BaseModel):
    valid: bool
    reason: str
    message: str
    discount_tiyin: int


class PackageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: UUID
    name: str = Field(min_length=1, max_length=120)
    sessions: int = Field(ge=2, le=50)
    discount_percent: int = Field(ge=1, le=90)
    validity_days: int = Field(default=90, ge=7, le=365)


class PackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    sessions: int | None = Field(default=None, ge=2, le=50)
    discount_percent: int | None = Field(default=None, ge=1, le=90)
    validity_days: int | None = Field(default=None, ge=7, le=365)
    is_active: bool | None = None


class PackageOut(BaseModel):
    id: UUID
    doctor_id: UUID
    service_id: UUID
    service_name: str
    name: str
    sessions: int
    discount_percent: int
    validity_days: int
    is_active: bool
    unit_price_tiyin: int
    total_saving_tiyin: int


class EnrollmentOut(BaseModel):
    id: UUID
    package_id: UUID
    doctor_id: UUID
    service_id: UUID
    package_name: str
    discount_percent: int
    sessions_total: int
    sessions_left: int
    expires_at: datetime


class PromoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3, max_length=32, pattern=r"^\s*[A-Za-z0-9_-]+\s*$")
    kind: Literal["percent", "fixed"]
    value: int = Field(gt=0, description="percent: 1..100; fixed: tiyin")
    max_discount_tiyin: int | None = Field(default=None, gt=0)
    min_amount_tiyin: int = Field(default=0, ge=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_uses: int | None = Field(default=None, gt=0)
    max_uses_per_user: int = Field(default=1, gt=0)
    first_booking_only: bool = False
    doctor_id: UUID | None = None
    borne_by: Literal["platform", "doctor"] = "platform"
    is_active: bool = True


class PromoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int | None = Field(default=None, gt=0)
    max_discount_tiyin: int | None = Field(default=None, gt=0)
    min_amount_tiyin: int | None = Field(default=None, ge=0)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_uses: int | None = Field(default=None, gt=0)
    max_uses_per_user: int | None = Field(default=None, gt=0)
    first_booking_only: bool | None = None
    borne_by: Literal["platform", "doctor"] | None = None
    is_active: bool | None = None


class PromoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    kind: str
    value: int
    max_discount_tiyin: int | None
    min_amount_tiyin: int
    valid_from: datetime | None
    valid_until: datetime | None
    max_uses: int | None
    max_uses_per_user: int
    first_booking_only: bool
    doctor_id: UUID | None
    borne_by: str
    is_active: bool
    uses: int = 0
    created_at: datetime
