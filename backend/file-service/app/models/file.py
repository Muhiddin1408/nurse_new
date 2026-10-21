"""Fayl metadata'si. Obyektning o'zi — S3'da; bu yerda kim yukladi, qaysi tur, holat va kimga biriktirilgan."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# pending   — URL berildi, yuklash kutilmoqda
# uploaded  — complete: hajm va tur tekshirildi (rasm tozalandi, thumbnail yasaldi)
# attached  — egasi servis (`attached_service`, `attached_ref`) o'zlashtirdi; endi avtomatik o'chirilmaydi
# rejected  — tekshiruvdan o'tmadi (obyekt o'chirildi)
# deleted   — o'chirildi (egasi, release yoki 24 soatlik tozalash)
STATUSES = ("pending", "uploaded", "attached", "rejected", "deleted")


class File(Base):
    __tablename__ = "files"
    __table_args__ = (
        Index("ix_files_owner", "owner_user_id", "created_at"),
        # Tozalash so'rovi faqat biriktirilmaganlarni ko'radi
        Index("ix_files_unattached", "created_at", postgresql_where=text("status IN ('pending', 'uploaded')")),
        Index("ix_files_attached_ref", "attached_service", "attached_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(8), nullable=False)
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    thumbnail_key: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size: Mapped[int | None] = mapped_column(BigInteger)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    reject_reason: Mapped[str | None] = mapped_column(String(64))
    attached_service: Mapped[str | None] = mapped_column(String(64))
    attached_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
