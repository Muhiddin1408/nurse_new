from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Role = Literal["client", "doctor", "clinic_admin"]


class OtpRequest(BaseModel):
    phone: str = Field(max_length=32, examples=["+998901234567"])
    language: Literal["uz", "ru"] = "uz"


class OtpRequested(BaseModel):
    detail: str = "Kod yuborildi"
    expires_in: int
    retry_after: int
    # Faqat lokal/dev + SMS_PROVIDER=console (OTP_DEBUG_ECHO)
    debug_code: str | None = None


class OtpVerify(BaseModel):
    phone: str = Field(max_length=32)
    code: str = Field(pattern=r"^\d{6}$")


class RefreshRequest(BaseModel):
    refresh: str = Field(min_length=20, max_length=128)


class SwitchRoleRequest(RefreshRequest):
    role: Role


class TokenPairOut(BaseModel):
    access: str
    refresh: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 — sxema nomi, sir emas
    expires_in: int


class AuthUser(BaseModel):
    id: UUID
    phone: str
    roles: list[str]
    active_role: str
    # Monolit javobi bilan moslik (frontend `available_roles` / `role` ni o'qiydi)
    available_roles: list[str]
    role: str
    doctor_id: UUID | None = None


class LoginOut(TokenPairOut):
    is_new_user: bool
    user: AuthUser
    # Monolit javobi bilan moslik: `{"tokens": {"access", "refresh"}}`
    tokens: dict[str, str]


class Me(BaseModel):
    id: UUID
    phone: str | None
    roles: list[str]
    active_role: str
    doctor_id: UUID | None
    created_at: datetime


class AdminUserOut(BaseModel):
    id: UUID
    phone: str | None
    is_active: bool
    deactivated_reason: str | None
    deactivated_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime


class AdminUserList(BaseModel):
    items: list[AdminUserOut]
    total: int
    limit: int
    offset: int


class BlockRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
