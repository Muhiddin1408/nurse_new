"""notification-service ma'lumotlari.

- `contacts` — kimga va qaysi kanal orqali (proyeksiya: `user.*` eventlari + foydalanuvchi sozlamalari).
- `doctor_contacts` — `doctor_id → user_id` (catalog eventlaridan): shifokorga xabar uning user kontakti orqali.
- `booking_projection` — eslatmalar va kunlik digest uchun bronning oxirgi holati (`booking.*`).
- `notifications` — XABARLAR OUTBOX'I: handler faqat qator yozadi (event bilan bitta tranzaksiyada),
  dispatcher yuboradi va qayta uriniladi. Rejalashtirilgan eslatmalar ham shu jadvalda (`send_at`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, SmallInteger, String, Text, func
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Contact(Base):
    __tablename__ = "contacts"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    # `user.registered` dan; o'chirishda NULL
    phone: Mapped[str | None] = mapped_column(String(20))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    language: Mapped[str] = mapped_column(String(2), nullable=False, default="uz", server_default="uz")
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), nullable=False, default=lambda: ["client"], server_default=sql("'{client}'")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    # Sozlamalar (monolit C13): kanal ustuvorligi push > telegram > sms
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql("false"))
    # Rozilik huquqiy ahamiyatga ega — qachon berilgani/qaytarilgani saqlanadi
    marketing_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DoctorContact(Base):
    __tablename__ = "doctor_contacts"

    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PushDevice(Base):
    """FCM tokeni. Bitta foydalanuvchi — bir nechta qurilma; token boshqa akkauntga o'tsa — ko'chadi."""

    __tablename__ = "push_devices"
    __table_args__ = (Index("ix_push_devices_user", "user_id", postgresql_where=sql("is_active")),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    token: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Template(Base):
    """Admin o'zgartirgan matn (deploy'siz). Bo'lmasa — koddagi standart (`app/services/templates.py`).
    Parametrlar (`{number}`, `{when}`) standart bilan bir xil bo'lishi shart — saqlashda tekshiriladi."""

    __tablename__ = "templates"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    language: Mapped[str] = mapped_column(String(2), primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=sql("true"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BookingProjection(Base):
    __tablename__ = "booking_projection"
    __table_args__ = (Index("ix_booking_projection_doctor_start", "doctor_id", "start_at"),)

    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    place: Mapped[str] = mapped_column(String(16), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# pending   — navbatda (send_at kelganda yuboriladi)
# sending   — dispatcher oldi (locked_until gacha; jarayon o'lsa — qayta olinadi)
# sent      — kamida bitta kanal orqali yetkazildi
# skipped   — yuborish kerak emas (bron holati mos emas, kontakt yo'q, marketing roziligisiz)
# cancelled — bron bekor qilindi / akkaunt o'chirildi
# failed    — barcha urinishlar tugadi
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # Dispatcher so'rovi faqat shu qisman indeksga tayanadi
        Index("ix_notifications_due", "send_at", postgresql_where=sql("status IN ('pending', 'sending')")),
        Index("ix_notifications_booking", "booking_id", postgresql_where=sql("booking_id IS NOT NULL")),
        Index("ix_notifications_user", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audience: Mapped[str] = mapped_column(String(8), nullable=False)  # user | ops
    user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    marketing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=sql("false"))
    # Takror yaratmaslik: `reminder_24h:<booking>`, `digest:<doctor>:<sana>`, `event:<event_id>:<template>`
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    # Eslatma turi — yuborish paytidagi bron holati tekshiruvi uchun (reminder | review_request)
    kind: Mapped[str | None] = mapped_column(String(24))
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    channel: Mapped[str | None] = mapped_column(String(10))
    last_error: Mapped[str | None] = mapped_column(Text)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
