from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ReviewIn(BaseModel):
    booking_id: UUID
    rating: int = Field(ge=1, le=5)
    comment: str = Field("", max_length=4000)
    is_anonymous: bool = False
    author_name: str = Field("", max_length=128, description='Profil ismi; ommaviy sahifada "Aziz T." ko\'rinishida')


class PublicReviewOut(BaseModel):
    """Ommaviy ko'rinish: mijoz ID'si, bron raqami, moderatsiya ma'lumoti — YO'Q."""

    id: UUID
    doctor_id: UUID
    rating: int
    comment: str
    author: str | None
    created_at: datetime
    doctor_reply: str | None
    doctor_replied_at: datetime | None


class MyReviewOut(PublicReviewOut):
    booking_id: UUID
    status: str
    reject_reason: str


class AdminReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_id: UUID
    booking_number: str
    client_id: UUID
    doctor_id: UUID
    clinic_id: UUID | None
    rating: int
    comment: str
    is_anonymous: bool
    author_name: str
    status: str
    moderation_flags: list[str]
    moderated_by: UUID | None
    moderated_at: datetime | None
    reject_reason: str
    doctor_reply: str
    created_at: datetime


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
