from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ClinicRef(BaseModel):
    id: str
    name: str
    city: str


class DoctorCard(BaseModel):
    id: str
    full_name: str
    photo_file_id: str | None = None
    specialization_ids: list[str]
    specialization_names: list[str]
    experience_years: int
    languages: list[str]
    rating: float | None
    reviews_count: int
    accepts_home_visits: bool
    places: list[str] | None
    min_price_tiyin: int | None
    clinics: list[ClinicRef] | None
    next_available_at: datetime | None
    distance_km: float | None


class ClinicCard(BaseModel):
    id: str
    name: str
    city: str
    street: str
    rating: float | None
    reviews_count: int
    specialization_ids: list[str] | None
    specialization_names: list[str] | None
    doctors_count: int | None
    min_price_tiyin: int | None
    distance_km: float | None


class DoctorResults(BaseModel):
    engine: str
    degraded: bool
    total: int | None
    items: list[DoctorCard]


class ClinicResults(BaseModel):
    engine: str
    degraded: bool
    total: int | None
    items: list[ClinicCard]


class Suggestions(BaseModel):
    engine: str
    doctors: list[dict[str, Any]]
    clinics: list[dict[str, Any]]
    specializations: list[dict[str, Any]]


class ReindexOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    requested_by: UUID | None
    doctors_index: str
    clinics_index: str
    doctors_count: int
    clinics_count: int
    error: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
