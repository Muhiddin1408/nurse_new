"""auth-service ma'lumotlari: foydalanuvchi identifikatori (telefon), OTP, refresh sessiyalar va
rollar proyeksiyasi (catalog eventlaridan)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """Identifikator — telefon. Profil (ism, til) user-service'da, bu yerda faqat kirish uchun kerakli."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # O'chirilgan akkauntda NULL — shu raqam bilan keyin kirilsa butunlay yangi akkaunt yaratiladi.
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # deletion_requested | blocked_by_admin
    deactivated_reason: Mapped[str | None] = mapped_column(String(32))
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Keyingi kirishda tanlanadigan rol (switch-role eslab qoladi)
    preferred_role: Mapped[str] = mapped_column(String(20), nullable=False, default="client", server_default="client")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DoctorLink(Base):
    """`doctor.approved|suspended|reinstated` proyeksiyasi: `doctor` roli va `doctor_id` claim'i.

    `changed_at` — oxirgi qo'llangan eventning `occurred_at` i: retry tufayli kechikkan eski event
    yangi holatni ustidan yozmaydi."""

    __tablename__ = "doctor_links"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # approved | suspended
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClinicMembership(Base):
    """`clinic.member_added|removed` proyeksiyasi: faol a'zolik bo'lsa — `clinic_admin` roli.

    O'chirishda qator qoladi (`is_active=false`, tombstone) — tartibsiz kelgan eventlarni `changed_at`
    bo'yicha to'g'ri qo'llash uchun."""

    __tablename__ = "clinic_memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OtpCode(Base):
    """SMS kod. KOD OCHIQ SAQLANMAYDI — faqat HMAC: bazaga kirgan odam boshqa akkauntga kira olmasin."""

    __tablename__ = "otp_codes"
    __table_args__ = (
        Index("ix_otp_codes_phone_active", "phone", "created_at", postgresql_where=text("used_at IS NULL")),
        Index("ix_otp_codes_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RefreshToken(Base):
    """Opaque refresh token (faqat SHA-256 xeshi). Bitta qurilma sessiyasi = bitta `family_id`.

    Har yangilashda rotatsiya: eski qator `revoked_reason=rotated`. Rotatsiya qilingan token qayta
    kelsa — o'g'irlangan deb hisoblanadi va butun oila bekor qilinadi (ARCHITECTURE 5.1)."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    active_role: Mapped[str] = mapped_column(String(20), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # rotated | logout | logout_all | reuse_detected | deactivated
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(255))
