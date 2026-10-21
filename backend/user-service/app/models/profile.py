"""user-service ma'lumotlari: profil, bemorlar (oila a'zolari), manzillar, sevimli shifokorlar va
faol bronlar proyeksiyasi (`booking.*` eventlaridan)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Numeric, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Profile(Base):
    """`user_id` — auth-service'dagi ID (UUID bir xil). Telefon bu yerda SAQLANMAYDI — egasi auth."""

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    # Bildirishnomalar shu tilda (API javoblari tili — Accept-Language)
    preferred_language: Mapped[str] = mapped_column(String(2), nullable=False, default="uz", server_default="uz")
    avatar_file_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    avatar_thumbnail_url: Mapped[str | None] = mapped_column(String(512))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Patient(Base):
    """Bemor — foydalanuvchining o'zi yoki oila a'zosi. Bron Patient'ga bog'lanadi.
    Bronlari bor bemor jismonan o'chirilmaydi (`is_deleted`) — tarix buzilmaydi."""

    __tablename__ = "patients"
    __table_args__ = (Index("ix_patients_user", "user_id", postgresql_where=text("NOT is_deleted")),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    relation: Mapped[str] = mapped_column(String(50), nullable=False, default="", server_default="")
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)
    weight_kg: Mapped[int | None] = mapped_column(SmallInteger)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Address(Base):
    """Uy chaqiruvi manzili. Bron o'z nusxasini (snapshot) saqlaydi — manzil har doim yumshoq o'chiriladi."""

    __tablename__ = "addresses"
    __table_args__ = (
        Index("ix_addresses_user", "user_id", postgresql_where=text("NOT is_deleted")),
        # Bir foydalanuvchida faqat BITTA standart manzil
        Index(
            "uq_addresses_one_default",
            "user_id",
            unique=True,
            postgresql_where=text("is_default AND NOT is_deleted"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    district: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    street: Mapped[str] = mapped_column(String(255), nullable=False)
    entrance: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    floor: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    apartment: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Favorite(Base):
    """Sevimli shifokor. `doctor_id` — catalog-service ID'si (FK yo'q: boshqa servis bazasi)."""

    __tablename__ = "favorites"
    __table_args__ = (Index("ix_favorites_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BookingState(Base):
    """Proyeksiya (ARCHITECTURE 9.1): bronning oxirgi ma'lum holati. Akkaunt/bemor o'chirishda
    "faol bron bormi" savoliga javob. Yakuniy holat (`cancelled`, `expired`, `completed`, `no_show`)
    keyin kelgan eski event bilan qaytarilmaydi."""

    __tablename__ = "booking_states"
    __table_args__ = (
        Index("ix_booking_states_user_active", "user_id", postgresql_where=text("is_active")),
        Index("ix_booking_states_patient_active", "patient_id", postgresql_where=text("is_active")),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
