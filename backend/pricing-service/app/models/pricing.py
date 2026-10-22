"""pricing-service ma'lumotlari. Pul — tiyin (BIGINT).

- `services_cache` — catalog xizmatlari proyeksiyasi (`service.*` eventlari + catalog'dan zaxira o'qish).
- `client_bookings` — mijoz statistikasi proyeksiyasi (`booking.*`): birinchi bron, takroriy tashrif, no-show.
- `promo_codes` / `promo_redemptions` — promo kodlar va ishlatilishi.
- `packages` / `package_enrollments` / `package_uses` — seans paketlari (C12).

Band qilish holati (`state`): reserved (commit, bron hali yaratilmagan) → active (`booking.created`) →
released (bekor / muddat o'tdi / sweeper). Limitlar reserved+active bo'yicha sanaladi.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

RESERVED, ACTIVE, RELEASED = "reserved", "active", "released"


class ServiceCache(Base):
    __tablename__ = "services_cache"

    service_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    specialization_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    place: Mapped[str] = mapped_column(String(8), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    price_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Fiskal kodlar eventda yo'q — catalog'dan o'qilganda to'ldiriladi (bo'sh — admin standarti)
    mxik_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    package_code: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    vat_percent: Mapped[int | None] = mapped_column(SmallInteger)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClientBooking(Base):
    __tablename__ = "client_bookings"
    __table_args__ = (Index("ix_client_bookings_client", "client_id", "doctor_id"),)

    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    no_show_by: Mapped[str] = mapped_column(String(10), nullable=False, default="", server_default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # katta harfda
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # percent | fixed
    # percent: 1..100; fixed: tiyin
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_discount_tiyin: Mapped[int | None] = mapped_column(BigInteger)
    min_amount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    max_uses_per_user: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    first_booking_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    borne_by: Mapped[str] = mapped_column(String(10), nullable=False, default="platform", server_default="platform")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PromoRedemption(Base):
    __tablename__ = "promo_redemptions"
    __table_args__ = (
        Index("ix_promo_redemptions_promo_live", "promo_id", "client_id", postgresql_where=text("state <> 'released'")),
        Index("ix_promo_redemptions_reserved", "reserved_at", postgresql_where=text("state = 'reserved'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    promo_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("promo_codes.id"), nullable=False)
    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    client_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    amount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Package(Base):
    """Shifokor taklifi: N seans xizmatga X% chegirma (oldindan to'lov yo'q, chegirmani shifokor ko'taradi)."""

    __tablename__ = "packages"
    __table_args__ = (Index("ix_packages_doctor_active", "doctor_id", "is_active"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sessions: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    discount_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    validity_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=90, server_default="90")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PackageEnrollment(Base):
    __tablename__ = "package_enrollments"
    __table_args__ = (
        Index(
            "uq_package_enrollments_active_service",
            "client_id",
            "service_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    package_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("packages.id"), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    package_name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Taklif nusxasi (shifokor keyin o'zgartirsa ham yozilish shartlari saqlanadi)
    sessions_total: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    discount_percent: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PackageUse(Base):
    __tablename__ = "package_uses"
    __table_args__ = (
        Index("ix_package_uses_enrollment_live", "enrollment_id", postgresql_where=text("state <> 'released'")),
        Index("ix_package_uses_reserved", "reserved_at", postgresql_where=text("state = 'reserved'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    enrollment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("package_enrollments.id"), nullable=False
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    amount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
