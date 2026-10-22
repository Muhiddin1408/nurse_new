"""review-service ma'lumotlari: sharhlar (moderatsiya holat mashinasi, shifokor javobi) va sharh qoldirish mumkin
bo'lgan bronlar proyeksiyasi (`booking.completed` dan).

Reyting saqlanmaydi — har o'zgarishda published sharhlardan hisoblanib `rating.recalculated` bilan catalog'ga
ketadi (egasi catalog'da denormalizatsiya)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, SmallInteger, String, Text, func
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

PUBLISHED, PENDING, REJECTED = "published", "pending", "rejected"


class ReviewableBooking(Base):
    """`booking.completed` proyeksiyasi: egalik, shifokor, klinika va yakunlangan vaqt (sharh oynasi shundan)."""

    __tablename__ = "reviewable_bookings"

    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Review(Base):
    """Sharh (C11). Bitta bronga bitta (UNIQUE booking_id).

    pending (avto-filtr shubha qildi) ──moderator──> published | rejected
    published (toza) ──moderator (shikoyat bo'yicha olib tashlash)──> rejected
    Reyting faqat `published` lardan.
    """

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_1_5"),
        Index("ix_reviews_doctor_published", "doctor_id", "created_at", postgresql_where=sql("status = 'published'")),
        Index("ix_reviews_clinic_published", "clinic_id", "created_at", postgresql_where=sql("status = 'published'")),
        Index("ix_reviews_status_created", "status", "created_at"),
        Index("ix_reviews_client", "client_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    booking_number: Mapped[str] = mapped_column(String(20), nullable=False, default="", server_default="")
    client_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    doctor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    is_anonymous: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # Ommaviy ko'rinishdagi ism ("Aziz T.") — mijoz ilovasi profil ismidan yuboradi; ism servisda saqlanmaydi
    author_name: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=PUBLISHED)
    moderation_flags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    moderated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    doctor_reply: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    doctor_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
