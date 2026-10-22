"""catalog-service ma'lumotlari: mutaxassisliklar, klinikalar, xonalar, shifokorlar (onboarding va moderatsiya),
hujjatlar (file-service ID), klinika a'zolari, hamkorliklar va xizmatlar.

Pul — tiyin (BIGINT, ADR-0007). Fayllar — faqat file-service ID'si (obyektning o'zi S3'da).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

UUIDCol = PG_UUID(as_uuid=True)


class Specialization(Base):
    """Mutaxassislik + C6 bemor cheklovlari (yosh, jins)."""

    __tablename__ = "specializations"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name_ru: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    is_pediatric: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql("false"))
    accepts_children: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql("false"))
    min_patient_age: Mapped[int | None] = mapped_column(SmallInteger)
    max_patient_age: Mapped[int | None] = mapped_column(SmallInteger)
    allowed_gender: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Clinic(Base):
    """Hayot sikli: draft → active (publish) ↔ suspended. Faqat `active` ommaga ko'rinadi va bron qilinadi."""

    __tablename__ = "clinics"
    # updated_at (onupdate=now()) flushda RETURNING bilan qaytadi: "expired" atribut lazy-load bo'lmaydi
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 — SQLAlchemy deklarativ atributi
    __table_args__ = (Index("ix_clinics_status_city", "status", "city"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    phone: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    street: Mapped[str] = mapped_column(String(255), nullable=False)
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    status_reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    # {"0": ["08:00", "18:00"], ..., "6": null} — 0 = dushanba, null = yopiq (D7)
    working_hours: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    photo_url: Mapped[str | None] = mapped_column(String(512))
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    reviews_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rating_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Room(Base):
    """Klinika xonasi (D8). O'chirilmaydi — `is_active=false`."""

    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("clinic_id", "name", name="uq_rooms_clinic_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    floor: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    equipment: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list, server_default=sql("'{}'")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Doctor(Base):
    """Shifokor profili. Holat mashinasi (D2): draft → pending → approved | rejected → (tuzatib) pending;
    approved ↔ suspended. `user_id` — auth-service foydalanuvchisi (bitta user — bitta profil)."""

    __tablename__ = "doctors"
    # updated_at (onupdate=now()) flushda RETURNING bilan qaytadi: "expired" atribut lazy-load bo'lmaydi
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 — SQLAlchemy deklarativ atributi
    __table_args__ = (Index("ix_doctors_status_home", "status", "accepts_home_visits"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    education: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    experience_years: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    languages: Mapped[list[str]] = mapped_column(
        ARRAY(String(5)), nullable=False, default=list, server_default=sql("'{}'")
    )
    license_number: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    license_expires_at: Mapped[date | None] = mapped_column(Date)
    license_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", server_default="draft")
    status_reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moderated_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    # B10: klinikadagi qabul uchun to'lov rejimi (uy chaqiruvi — har doim oldindan to'lov)
    clinic_payment_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="prepaid", server_default="prepaid"
    )
    # C12: 14 kun ichida takroriy tashrif chegirmasi (shifokor hisobidan)
    follow_up_discount_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    accepts_home_visits: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql("false")
    )
    home_visit_radius_km: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=10, server_default="10")
    home_base_latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    home_base_longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    photo_url: Mapped[str | None] = mapped_column(String(512))
    rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    reviews_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rating_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DoctorSpecialization(Base):
    __tablename__ = "doctor_specializations"

    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("doctors.id", ondelete="CASCADE"), primary_key=True
    )
    specialization_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("specializations.id", ondelete="RESTRICT"), primary_key=True
    )


class DoctorDocument(Base):
    """Diplom, litsenziya va h.k. — fayl file-service'ning `private-documents` bucketida (faqat ID)."""

    __tablename__ = "doctor_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # diploma | license | certificate | passport
    file_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ClinicMembership(Base):
    """Klinika admini (D1). Admin API orqali `user_id` bo'yicha qo'shiladi; o'chirishda `is_active=false`."""

    __tablename__ = "clinic_memberships"
    # updated_at (onupdate=now()) flushda RETURNING bilan qaytadi: "expired" atribut lazy-load bo'lmaydi
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 — SQLAlchemy deklarativ atributi
    __table_args__ = (
        UniqueConstraint("user_id", "clinic_id", name="uq_clinic_memberships_user_clinic"),
        Index("ix_clinic_memberships_user_active", "user_id", postgresql_where=sql("is_active")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, nullable=False)
    clinic_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Affiliation(Base):
    """Shifokor–klinika (D7, ikki tomonlama rozilik): invited → active ↔ paused; → declined | ended.
    Rad etilgan/tugagan qator qayta taklifda qayta ishlatiladi (UNIQUE)."""

    __tablename__ = "affiliations"
    # updated_at (onupdate=now()) flushda RETURNING bilan qaytadi: "expired" atribut lazy-load bo'lmaydi
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 — SQLAlchemy deklarativ atributi
    __table_args__ = (
        UniqueConstraint("doctor_id", "clinic_id", name="uq_affiliations_doctor_clinic"),
        Index("ix_affiliations_clinic_status", "clinic_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False)
    clinic_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Service(Base):
    """Xizmat: shifokorniki (`doctor_id`) yoki klinikaniki (`clinic_id`, `doctor_id` NULL). Narx — tiyin.
    Bronlar narx nusxasini saqlaydi, shuning uchun narx o'zgarishi mavjud bronlarga ta'sir qilmaydi.
    Xizmat o'chirilmaydi — `is_active=false` (catalog qaysi xizmat bron qilinganini bilmaydi)."""

    __tablename__ = "services"
    # updated_at (onupdate=now()) flushda RETURNING bilan qaytadi: "expired" atribut lazy-load bo'lmaydi
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012 — SQLAlchemy deklarativ atributi
    __table_args__ = (
        CheckConstraint("clinic_id IS NOT NULL OR doctor_id IS NOT NULL", name="has_owner"),
        CheckConstraint("price_tiyin > 0", name="price_positive"),
        Index("ix_services_doctor", "doctor_id", postgresql_where=sql("doctor_id IS NOT NULL")),
        Index("ix_services_clinic", "clinic_id", postgresql_where=sql("clinic_id IS NOT NULL")),
        Index("ix_services_spec_place_active", "specialization_id", "place", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDCol, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("clinics.id", ondelete="CASCADE"))
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("doctors.id", ondelete="CASCADE"))
    specialization_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("specializations.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    place: Mapped[str] = mapped_column(String(8), nullable=False)  # clinic | home
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    # B12 fiskal kodlari — bo'sh bo'lsa admin `fiscal` sozlamasidagi standart (booking/payment qo'llaydi)
    mxik_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    package_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    vat_percent: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ServicePriceHistory(Base):
    """Narx o'zgarishlari jurnali (D5) — faqat qo'shiladi."""

    __tablename__ = "service_price_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    service_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    old_price_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    new_price_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    is_large_change: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
