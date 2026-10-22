from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Gender = Literal["male", "female"]
Language = Literal["uz", "ru"]


class MeOut(BaseModel):
    id: UUID
    phone: str | None
    roles: list[str]
    full_name: str
    preferred_language: Language
    avatar_url: str | None
    avatar_thumbnail_url: str | None
    created_at: datetime


class MeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=255)
    preferred_language: Language | None = None
    # null — avatarni olib tashlash; UUID — file-service'da yuklangan va `complete` qilingan fayl
    avatar_file_id: UUID | None = None


class PatientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=255)
    relation: str = Field(default="", max_length=50)
    birth_date: date
    gender: Gender
    weight_kg: int | None = Field(default=None, ge=1, le=400)


class PatientUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    relation: str | None = Field(default=None, max_length=50)
    birth_date: date | None = None
    gender: Gender | None = None
    weight_kg: int | None = Field(default=None, ge=1, le=400)


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    full_name: str
    relation: str
    birth_date: date
    gender: Gender
    weight_kg: int | None


class AddressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="", max_length=100)
    city: str = Field(min_length=1, max_length=100)
    district: str = Field(default="", max_length=100)
    street: str = Field(min_length=1, max_length=255)
    entrance: str = Field(default="", max_length=20)
    floor: str = Field(default="", max_length=20)
    apartment: str = Field(default="", max_length=20)
    comment: str = Field(default="", max_length=1000)
    latitude: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)
    is_default: bool = False


class AddressUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    street: str | None = Field(default=None, min_length=1, max_length=255)
    entrance: str | None = Field(default=None, max_length=20)
    floor: str | None = Field(default=None, max_length=20)
    apartment: str | None = Field(default=None, max_length=20)
    comment: str | None = Field(default=None, max_length=1000)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    is_default: bool | None = None


class AddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str
    city: str
    district: str
    street: str
    entrance: str
    floor: str
    apartment: str
    comment: str
    latitude: Decimal
    longitude: Decimal
    is_default: bool


class FavoriteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doctor_id: UUID
    created_at: datetime


class InternalPatient(BaseModel):
    """booking-service uchun: bron paytidagi snapshot shu maydonlardan olinadi."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    full_name: str
    relation: str
    birth_date: date
    gender: Gender
    weight_kg: int | None


class InternalAddress(AddressOut):
    user_id: UUID


class AdminProfileOut(BaseModel):
    user_id: UUID
    full_name: str
    preferred_language: str
    deleted_at: datetime | None
    patients: int
    addresses: int
    active_bookings: int
    created_at: datetime
