from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Place = Literal["clinic", "home"]
PaymentMode = Literal["prepaid", "deposit", "at_clinic"]
Language = Literal["uz", "ru", "en", "kk", "tg", "tr"]
DocumentKind = Literal["diploma", "license", "certificate", "passport"]
Gender = Literal["", "male", "female"]


def validate_working_hours(value: dict[str, Any]) -> dict[str, Any]:
    """{"0": ["08:00","18:00"], ..., "6": null} — 0 = dushanba; ochilish < yopilish."""
    for key, hours in value.items():
        if key not in {str(i) for i in range(7)}:
            raise ValueError(f"noto'g'ri kun: {key}")
        if hours is None:
            continue
        if not isinstance(hours, list) or len(hours) != 2:
            raise ValueError(f"{key}: [ochilish, yopilish] bo'lishi kerak")
        start, end = (time.fromisoformat(h) for h in hours)
        if start >= end:
            raise ValueError(f"{key}: ochilish yopilishdan oldin bo'lishi kerak")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- specializations -------------------------------------------------------------------------


class SpecializationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    name_ru: str
    slug: str
    is_pediatric: bool
    accepts_children: bool
    min_patient_age: int | None
    max_patient_age: int | None
    allowed_gender: str


class SpecializationIn(_Strict):
    name: str = Field(min_length=2, max_length=100)
    name_ru: str = Field(default="", max_length=100)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    is_pediatric: bool = False
    accepts_children: bool = False
    min_patient_age: int | None = Field(default=None, ge=0, le=120)
    max_patient_age: int | None = Field(default=None, ge=0, le=120)
    allowed_gender: Gender = ""


class SpecializationUpdate(_Strict):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    name_ru: str | None = Field(default=None, max_length=100)
    is_pediatric: bool | None = None
    accepts_children: bool | None = None
    min_patient_age: int | None = Field(default=None, ge=0, le=120)
    max_patient_age: int | None = Field(default=None, ge=0, le=120)
    allowed_gender: Gender | None = None


# --- clinics ---------------------------------------------------------------------------------


class ClinicOut(BaseModel):
    id: UUID
    name: str
    description: str
    phone: str
    city: str
    street: str
    latitude: Decimal
    longitude: Decimal
    working_hours: dict[str, Any]
    photo_url: str | None
    rating: Decimal | None
    reviews_count: int
    is_new: bool


class ClinicAdminOut(ClinicOut):
    status: str
    status_reason: str
    created_at: datetime


class ClinicCreate(_Strict):
    name: str = Field(min_length=2, max_length=255)
    description: str = Field(default="", max_length=4000)
    phone: str = Field(default="", max_length=20)
    city: str = Field(min_length=2, max_length=100)
    street: str = Field(min_length=2, max_length=255)
    latitude: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)
    working_hours: dict[str, Any] = Field(default_factory=dict)

    _wh = field_validator("working_hours")(validate_working_hours)


class ClinicAdminUpdate(_Strict):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    phone: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    street: str | None = Field(default=None, min_length=2, max_length=255)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    working_hours: dict[str, Any] | None = None

    _wh = field_validator("working_hours")(lambda v: v if v is None else validate_working_hours(v))


class ClinicProfileUpdate(_Strict):
    """Klinika admini — moderatsiyasiz maydonlar."""

    description: str | None = Field(default=None, max_length=4000)
    phone: str | None = Field(default=None, max_length=20)
    working_hours: dict[str, Any] | None = None
    photo_file_id: UUID | None = None

    _wh = field_validator("working_hours")(lambda v: v if v is None else validate_working_hours(v))


class StatusReason(_Strict):
    reason: str = Field(default="", max_length=2000)


class MemberIn(_Strict):
    user_id: UUID


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    clinic_id: UUID
    created_at: datetime


class RoomIn(_Strict):
    name: str = Field(min_length=1, max_length=100)
    floor: str = Field(default="", max_length=20)
    equipment: list[str] = Field(default_factory=list, max_length=30)


class RoomUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    floor: str | None = Field(default=None, max_length=20)
    equipment: list[str] | None = Field(default=None, max_length=30)
    is_active: bool | None = None


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    clinic_id: UUID
    name: str
    floor: str
    equipment: list[str]
    is_active: bool


# --- doctors ---------------------------------------------------------------------------------


class SpecializationBrief(BaseModel):
    id: UUID
    name: str
    name_ru: str


class DoctorListItem(BaseModel):
    id: UUID
    full_name: str
    experience_years: int
    rating: Decimal | None
    reviews_count: int
    is_new: bool
    specializations: list[SpecializationBrief]
    accepts_home_visits: bool
    photo_url: str | None
    distance_km: float | None = None


class DoctorDetail(DoctorListItem):
    bio: str
    education: str
    languages: list[str]
    license_number: str
    home_visit_radius_km: int
    clinics: list[ClinicOut]


class OnboardingProfile(_Strict):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    bio: str | None = Field(default=None, max_length=4000)
    education: str | None = Field(default=None, max_length=4000)
    experience_years: int | None = Field(default=None, ge=0, le=70)
    languages: list[Language] | None = Field(default=None, max_length=6)
    license_number: str | None = Field(default=None, max_length=100)
    license_expires_at: date | None = None
    accepts_home_visits: bool | None = None
    home_visit_radius_km: int | None = Field(default=None, ge=1, le=100)
    specialization_ids: list[UUID] | None = Field(default=None, max_length=5)


class DoctorPrivate(BaseModel):
    """Shifokorning o'zi ko'radigan profil (moderatsiya holati bilan)."""

    id: UUID
    user_id: UUID
    status: str
    status_reason: str
    full_name: str
    bio: str
    education: str
    experience_years: int
    languages: list[str]
    license_number: str
    license_expires_at: date | None
    accepts_home_visits: bool
    home_visit_radius_km: int
    home_base_latitude: Decimal | None
    home_base_longitude: Decimal | None
    clinic_payment_mode: str
    follow_up_discount_percent: int
    photo_url: str | None
    specialization_ids: list[UUID]
    submitted_at: datetime | None


class OnboardingStatus(BaseModel):
    status: str
    reason: str
    missing: list[str]
    submitted_at: datetime | None
    sla_due_at: datetime | None


class DocumentIn(_Strict):
    kind: DocumentKind
    file_id: UUID


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    file_id: UUID
    content_type: str
    created_at: datetime


class DoctorProfileUpdate(_Strict):
    bio: str | None = Field(default=None, max_length=4000)
    education: str | None = Field(default=None, max_length=4000)
    languages: list[Language] | None = Field(default=None, max_length=6)
    accepts_home_visits: bool | None = None
    home_visit_radius_km: int | None = Field(default=None, ge=1, le=100)
    follow_up_discount_percent: int | None = Field(default=None, ge=0, le=100)
    home_base_latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    home_base_longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    clinic_payment_mode: PaymentMode | None = None
    photo_file_id: UUID | None = None


class LicenseUpdate(_Strict):
    license_number: str = Field(min_length=1, max_length=100)
    license_expires_at: date


class ModerationDoctor(DoctorPrivate):
    documents: list[DocumentOut]
    moderated_at: datetime | None
    sla_due_at: datetime | None


class SignedUrlOut(BaseModel):
    url: str
    expires_in: int | None


# --- affiliations ----------------------------------------------------------------------------


class InviteIn(_Strict):
    doctor_id: UUID
    position: str = Field(default="", max_length=100)


class AffiliationOut(BaseModel):
    affiliation_id: UUID
    clinic_id: UUID
    doctor_id: UUID
    status: str
    position: str
    updated_at: datetime


class DoctorAffiliationOut(AffiliationOut):
    clinic_name: str
    city: str
    street: str


class ClinicDoctorOut(AffiliationOut):
    doctor_name: str
    doctor_status: str


# --- services --------------------------------------------------------------------------------


class ServiceIn(_Strict):
    specialization_id: UUID
    name: str = Field(min_length=2, max_length=255)
    name_ru: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=4000)
    place: Place
    duration_minutes: int = Field(ge=5, le=480)
    price_tiyin: int = Field(gt=0, le=10_000_000_000)


class ServiceUpdate(_Strict):
    specialization_id: UUID | None = None
    name: str | None = Field(default=None, min_length=2, max_length=255)
    name_ru: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    place: Place | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    price_tiyin: int | None = Field(default=None, gt=0, le=10_000_000_000)
    is_active: Literal[True] | None = Field(default=None, description="Qayta faollashtirish; o'chirish — DELETE")
    confirm_large_change: bool = False


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doctor_id: UUID | None
    clinic_id: UUID | None
    specialization_id: UUID
    name: str
    name_ru: str
    description: str
    place: str
    duration_minutes: int
    price_tiyin: int
    is_active: bool


class PublicServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    name_ru: str
    place: str
    duration_minutes: int
    price_tiyin: int
    clinic_id: UUID | None


class PriceHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    old_price_tiyin: int
    new_price_tiyin: int
    is_large_change: bool
    created_at: datetime


# --- internal --------------------------------------------------------------------------------


class Point(BaseModel):
    lat: float
    lng: float


class Bookability(BaseModel):
    doctor_id: UUID
    user_id: UUID
    status: str
    bookable: bool
    full_name: str
    accepts_home_visits: bool
    home_visit_radius_km: int
    home_base: Point | None
    clinic_payment_mode: str
    follow_up_discount_percent: int
    specialization_ids: list[UUID]
    clinic_ids: list[UUID]


class PatientRules(BaseModel):
    is_pediatric: bool
    accepts_children: bool
    min_patient_age: int | None
    max_patient_age: int | None
    allowed_gender: str


class InternalService(BaseModel):
    id: UUID
    doctor_id: UUID | None
    clinic_id: UUID | None
    specialization_id: UUID
    name: str
    name_ru: str
    place: str
    duration_minutes: int
    price_tiyin: int
    is_active: bool
    mxik_code: str
    package_code: str
    vat_percent: int | None
    patient_rules: PatientRules


class InternalServices(BaseModel):
    items: list[InternalService]


class AccessOut(BaseModel):
    allowed: bool
    role: str | None
    clinic_ids: list[UUID]


class InternalClinic(BaseModel):
    id: UUID
    name: str
    city: str
    street: str
    latitude: float
    longitude: float
    status: str
    working_hours: dict[str, Any]


class InternalRoom(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    clinic_id: UUID
    name: str
    is_active: bool


# --- search-service reindex eksporti (/internal/v1/export/*) ---------------------------------------------


class ExportService(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doctor_id: UUID | None
    clinic_id: UUID | None
    specialization_id: UUID
    name: str
    name_ru: str
    place: str
    duration_minutes: int
    price_tiyin: int


class ExportAffiliation(BaseModel):
    affiliation_id: UUID
    clinic_id: UUID
    status: str


class ExportDoctor(BaseModel):
    doctor_id: UUID
    user_id: UUID
    full_name: str
    status: str
    specialization_ids: list[UUID]
    experience_years: int
    languages: list[str]
    accepts_home_visits: bool
    home_visit_radius_km: int
    home_base: Point | None
    photo_file_id: UUID | None
    rating: float | None
    reviews_count: int
    affiliations: list[ExportAffiliation]
    services: list[ExportService]


class ExportClinic(BaseModel):
    clinic_id: UUID
    name: str
    city: str
    street: str
    latitude: float
    longitude: float
    status: str
    working_hours: dict[str, Any] | list[Any]
    rating: float | None
    reviews_count: int
    services: list[ExportService]


class ExportDoctors(BaseModel):
    items: list[ExportDoctor]
    next_after: UUID | None = Field(description="Keyingi sahifa uchun `after`; None — oxirgi sahifa")


class ExportClinics(BaseModel):
    items: list[ExportClinic]
    next_after: UUID | None


class ExportSpecialization(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    name_ru: str
    slug: str


class ExportSpecializations(BaseModel):
    items: list[ExportSpecialization]
