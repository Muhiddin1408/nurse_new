from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress


class LoginRequest(BaseModel):
    login: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MfaChallengeOut(BaseModel):
    mfa_token: str
    expires_in: int
    next: Literal["totp"] = "totp"


class TotpRequest(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=128)
    code: str = Field(pattern=r"^\d{6}$")


class AdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    login: str
    full_name: str
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class AdminDetail(AdminOut):
    role_ids: list[UUID]
    permissions: list[str]


class AdminSessionOut(BaseModel):
    access: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 — sxema nomi, sir emas
    expires_in: int
    admin: AdminOut
    permissions: list[str]


class AdminCreate(BaseModel):
    login: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._@+-]+$")
    full_name: str = Field(default="", max_length=255)
    password: str = Field(min_length=1, max_length=256)
    role_ids: list[UUID] = Field(default_factory=list)


class AdminUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    role_ids: list[UUID] | None = None


class EnrollmentOut(BaseModel):
    """`totp_uri` faqat shu javobda — QR qilib admin'ga bering, saqlamang."""

    admin: AdminOut
    totp_uri: str


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str
    permissions: list[str]


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_-]+$")
    description: str = Field(default="", max_length=255)
    permissions: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: UUID
    occurred_at: datetime
    producer: str
    actor_id: UUID | None
    actor_type: str
    actor_role: str
    action: str
    object_type: str
    object_id: str
    before: Any
    after: Any
    ip: IPvAnyAddress | None
    request_id: str | None


class AuditPage(BaseModel):
    items: list[AuditOut]
    next_before_id: int | None


class SettingsOut(BaseModel):
    version: int
    values: dict[str, Any]


class SettingsUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    values: dict[str, dict[str, Any]]
