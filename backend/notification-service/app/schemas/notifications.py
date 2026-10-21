from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PreferencesOut(BaseModel):
    push_enabled: bool
    telegram_enabled: bool
    sms_enabled: bool
    marketing_opt_in: bool
    telegram_linked: bool


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    push_enabled: bool | None = None
    telegram_enabled: bool | None = None
    sms_enabled: bool | None = None
    marketing_opt_in: bool | None = None


class DeviceIn(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    platform: Literal["android", "ios"]


class DeviceDelete(BaseModel):
    token: str = Field(min_length=10, max_length=512)


class LinkTokenOut(BaseModel):
    url: str
    expires_in: int
    linked: bool


class TemplateOut(BaseModel):
    key: str
    language: str
    default_text: str
    override_text: str | None
    is_active: bool
    placeholders: list[str]


class TemplateUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    is_active: bool = True


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    audience: str
    user_id: UUID | None
    template: str
    status: str
    channel: str | None
    attempts: int
    last_error: str | None
    booking_id: UUID | None
    send_at: datetime
    sent_at: datetime | None
    created_at: datetime
    params: dict[str, Any]
