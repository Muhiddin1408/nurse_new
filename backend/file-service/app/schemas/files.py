from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class UploadRequest(BaseModel):
    purpose: str = Field(max_length=32, examples=["avatar"])
    content_type: str = Field(max_length=64, examples=["image/jpeg"])
    size: int = Field(gt=0, description="Bayt")
    filename: str = Field(default="file", max_length=255)


class UploadOut(BaseModel):
    file_id: UUID
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_in: int


class FileOut(BaseModel):
    id: UUID
    purpose: str
    visibility: str
    status: str
    content_type: str
    size: int | None
    original_name: str
    url: str | None = None
    thumbnail_url: str | None = None
    created_at: datetime


class SignedUrlOut(BaseModel):
    url: str
    expires_in: int | None


class AttachRequest(BaseModel):
    owner_user_id: UUID
    owner_ref: str = Field(min_length=1, max_length=128)
    purpose: str | None = Field(default=None, max_length=32)


class ReleaseRequest(BaseModel):
    owner_ref: str = Field(min_length=1, max_length=128)
