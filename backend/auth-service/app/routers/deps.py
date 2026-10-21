"""Router dependency'lari: sessiya, rate limiter, SMS client (lifespan'da yaratiladi)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.context import client_ip
from medbron_common.sms import SmsClient

from app.core.database import get_session
from app.services.ratelimit import OtpRateLimiter
from app.services.sessions import ClientInfo

Session = Annotated[AsyncSession, Depends(get_session)]


def get_limiter(request: Request) -> OtpRateLimiter:
    limiter: OtpRateLimiter = request.app.state.otp_limiter
    return limiter


def get_sms(request: Request) -> SmsClient:
    sms: SmsClient = request.app.state.sms
    return sms


def get_client_info(request: Request) -> ClientInfo:
    return ClientInfo(ip=client_ip(request), user_agent=request.headers.get("user-agent"))


Limiter = Annotated[OtpRateLimiter, Depends(get_limiter)]
Sms = Annotated[SmsClient, Depends(get_sms)]
Client = Annotated[ClientInfo, Depends(get_client_info)]
