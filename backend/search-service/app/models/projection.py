"""search-service proyeksiyasi (ARCHITECTURE 9.1): Elasticsearch hujjatlari SHU jadvallardan quriladi.

Eventlar qisman (masalan `service.updated` faqat bitta xizmat, `clinic.updated` ko'p shifokorga ta'sir qiladi) —
shuning uchun haqiqat Postgres'da, ES esa undan to'liq qayta tiklanadigan o'qish modeli.

Tartibsizlikdan himoya: har jihat (profil, holat, reyting, bo'sh vaqt) o'z `*_at` vaqtiga ega — eventning
`occurred_at` i undan eski bo'lsa, o'zgarish qo'llanmaydi. Reindex snapshot'i ham shu vaqtlar bilan solishtiriladi.
`doc_version` — ES external versiyasi: qator qulfi ostida oshadi, eski hujjat yangisini ustidan yozmaydi."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, String, func
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

EPOCH = "'1970-01-01T00:00:00Z'"

# Shifokor holatlari: catalog'dan (approved/suspended) + `unknown` (event approved'dan oldin keldi),
# `removed` (reindex snapshot'ida yo'q — rad etilgan/o'chirilgan)
APPROVED, SUSPENDED, UNKNOWN, REMOVED = "approved", "suspended", "unknown", "removed"


def _ts(**kw: Any) -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=sql(EPOCH), **kw)


class DoctorRow(Base):
    __tablename__ = "doctors"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UNKNOWN, server_default=UNKNOWN)
    specialization_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    experience_years: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    languages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    accepts_home_visits: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    home_visit_radius_km: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Uy chaqiruvi bazasi eventlarda yo'q — faqat reindex snapshot'idan
    home_lat: Mapped[float | None] = mapped_column(Float)
    home_lng: Mapped[float | None] = mapped_column(Float)
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profile_at: Mapped[datetime] = _ts()
    status_at: Mapped[datetime] = _ts()
    rating_at: Mapped[datetime] = _ts()
    availability_at: Mapped[datetime] = _ts()
    doc_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


class ClinicRow(Base):
    __tablename__ = "clinics"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    city: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    street: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UNKNOWN, server_default=UNKNOWN)
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    info_at: Mapped[datetime] = _ts()
    status_at: Mapped[datetime] = _ts()
    rating_at: Mapped[datetime] = _ts()
    doc_version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")


class AffiliationRow(Base):
    __tablename__ = "affiliations"
    __table_args__ = (Index("ix_affiliations_doctor", "doctor_id"), Index("ix_affiliations_clinic", "clinic_id"))

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    clinic_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # active | paused | ended
    at: Mapped[datetime] = _ts()


class ServiceRow(Base):
    __tablename__ = "services"
    __table_args__ = (
        Index("ix_services_doctor", "doctor_id", postgresql_where=sql("doctor_id IS NOT NULL")),
        Index("ix_services_clinic", "clinic_id", postgresql_where=sql("clinic_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    specialization_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    place: Mapped[str] = mapped_column(String(8), nullable=False, default="clinic", server_default="clinic")
    price_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    at: Mapped[datetime] = _ts()


class SpecializationRow(Base):
    """Mutaxassisliklar lug'ati (nomlar eventlarda yo'q) — catalog eksportidan: reindex va kunlik sinxron."""

    __tablename__ = "specializations"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    slug: Mapped[str] = mapped_column(String(100), nullable=False)


class ReindexRun(Base):
    """Admin reindex so'rovlari (audit izi ham shu: kim, qachon, natija). Bir vaqtda bitta `pending|running`."""

    __tablename__ = "reindex_runs"
    __table_args__ = (
        Index("uq_reindex_runs_active", "kind", unique=True, postgresql_where=sql("status IN ('pending', 'running')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="all", server_default="all")
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending | running | done | failed
    requested_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    requested_ip: Mapped[str | None] = mapped_column(String(64))
    doctors_index: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    clinics_index: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    doctors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    clinics_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str] = mapped_column(String(2000), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
