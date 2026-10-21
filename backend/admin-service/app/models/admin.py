"""admin-service ma'lumotlari: admin akkauntlari, rollar, audit jurnali, global sozlamalar."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Admin(Base):
    """Admin — user'dan ALOHIDA identity (ADR-0005): login + argon2 parol + TOTP majburiy."""

    __tablename__ = "admins"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # kichik harflarda
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    totp_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    # Oxirgi ishlatilgan TOTP qadami — bitta kodni ikki marta ishlatib bo'lmaydi (replay)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AdminRole(Base):
    __tablename__ = "admin_roles"

    admin_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("admins.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True
    )


class AuditLog(Base):
    """`audit.recorded` eventlari (barcha servislardan). Faqat qo'shiladi — o'zgartirilmaydi, o'chirilmaydi
    (qonuniy saqlash: 5 yil, ARCHITECTURE 9.2). `event_id` UNIQUE — takroriy yetkazish ikkinchi qator bermaydi."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_object", "object_type", "object_id"),
        Index("ix_audit_log_actor", "actor_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    producer: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[Any] = mapped_column(JSONB)
    after: Mapped[Any] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(INET)
    request_id: Mapped[str | None] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PlatformSetting(Base):
    """Bitta bo'lim (`cancellation`, `payment`, ...) — qiymat `PlatformSettings` sxemasi bo'yicha tekshirilgan."""

    __tablename__ = "platform_settings"

    section: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SettingsVersion(Base):
    """Yagona qator: sozlamalar versiyasi (ETag, optimistic concurrency, `admin.settings_changed.version`)."""

    __tablename__ = "settings_version"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
