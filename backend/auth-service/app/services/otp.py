"""Telefon + OTP orqali kirish (ARCHITECTURE 6.1). Ro'yxatdan o'tish va kirish — BITTA oqim:
raqam tizimda bo'lmasa, user shu yerda yaratiladi va `user.registered` outbox'ga tushadi.

OTP SMS notification-service orqali EMAS — to'g'ridan-to'g'ri provayderga (ADR-0004): mijoz kodni
hozir kutyapti, login yo'liga asinxron navbat qo'shilmaydi.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.db import transaction
from medbron_common.events import Events
from medbron_common.phone import InvalidPhone, mask_phone, normalize_uz_phone
from medbron_common.sms import SmsClient, SmsSendError

from app.core.config import get_settings
from app.events import outbox
from app.metrics import OTP_REQUESTS, OTP_VERIFY
from app.models import OtpCode
from app.repositories import identity as repo
from app.services import sessions
from app.services.errors import (
    AccountBlocked,
    AttemptsExceeded,
    InvalidCode,
    InvalidPhoneError,
    RateLimiterUnavailable,
    SmsUnavailable,
    TooManyRequests,
)
from app.services.ratelimit import OtpRateLimiter
from app.services.roles import compute_roles

logger = logging.getLogger(__name__)

OTP_TEXT = {
    # ⚠️ Eskiz'da moderatsiyadan o'tgan matn — o'zgartirishdan oldin Eskiz kabinetida tasdiqlating.
    "uz": "MedBron tasdiqlash kodi: {code}. Hech kimga aytmang.",
    "ru": "Код подтверждения MedBron: {code}. Никому не сообщайте.",
}


@dataclass(frozen=True)
class OtpSent:
    expires_in: int
    retry_after: int
    debug_code: str | None


@dataclass(frozen=True)
class LoginResult:
    pair: sessions.TokenPair
    is_new_user: bool
    phone: str


def _phone(raw: str) -> str:
    try:
        return normalize_uz_phone(raw)
    except InvalidPhone as exc:
        raise InvalidPhoneError() from exc


def code_hash(phone: str, code: str) -> str:
    settings = get_settings()
    return hmac.new(settings.otp_secret, f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


async def request_otp(
    session: AsyncSession,
    limiter: OtpRateLimiter,
    sms: SmsClient,
    raw_phone: str,
    *,
    ip: str | None,
    language: str = "uz",
) -> OtpSent:
    """Javob raqam tizimda bor-yo'qligidan qat'i nazar BIR XIL (raqamlar ro'yxatini terib bo'lmasin)."""
    settings = get_settings()
    try:
        phone = _phone(raw_phone)
    except InvalidPhoneError:
        OTP_REQUESTS.labels(result="rejected_country").inc()
        raise
    try:
        await limiter.acquire_send(phone, ip)
    except TooManyRequests:
        OTP_REQUESTS.labels(result="rate_limited").inc()
        raise
    except RateLimiterUnavailable:
        OTP_REQUESTS.labels(result="limiter_unavailable").inc()
        raise

    now = datetime.now(UTC)
    code = f"{secrets.randbelow(1_000_000):06d}"
    otp = OtpCode(
        phone=phone,
        code_hash=code_hash(phone, code),
        expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
        ip_address=ip,
        created_at=now,
    )
    async with transaction(session):
        await repo.invalidate_active_otps(session, phone, now)
        session.add(otp)

    try:
        await sms.send(phone, OTP_TEXT.get(language, OTP_TEXT["uz"]).format(code=code))
    except SmsSendError as exc:
        # Kod mijozga yetmadi — yozuv o'chiriladi va limitlar qaytariladi: provayder tiklanganda
        # mijoz darhol qayta so'ray olsin.
        async with transaction(session):
            await repo.delete_otp(session, otp.id)
        await limiter.release_send(phone, ip)
        OTP_REQUESTS.labels(result="sms_failed").inc()
        logger.error("otp sms failed", extra={"phone": mask_phone(phone), "error": str(exc)})
        raise SmsUnavailable() from exc

    OTP_REQUESTS.labels(result="sent").inc()
    return OtpSent(
        expires_in=settings.otp_ttl_seconds,
        retry_after=settings.otp_resend_cooldown_seconds,
        debug_code=code if settings.otp_debug_echo else None,
    )


async def verify_otp(
    session: AsyncSession,
    limiter: OtpRateLimiter,
    raw_phone: str,
    code: str,
    *,
    client: sessions.ClientInfo,
) -> LoginResult:
    settings = get_settings()
    phone = _phone(raw_phone)
    await limiter.check_verify_allowed(phone)
    now = datetime.now(UTC)

    # Noto'g'ri kod / urinish tugadi — hisoblagichlar baribir COMMIT bo'ladi (aks holda cheksiz taxmin).
    async with transaction(session, commit_on=(InvalidCode, AttemptsExceeded, AccountBlocked)):
        otp = await repo.latest_active_otp(session, phone)
        if otp is None or otp.expires_at <= now:
            await limiter.register_failed_verify(phone)
            OTP_VERIFY.labels(result="expired").inc()
            raise InvalidCode()
        # Urinish AVVAL sanaladi — tekshiruvdan keyin emas.
        otp.attempts += 1
        if otp.attempts > settings.otp_max_attempts:
            otp.used_at = now
            OTP_VERIFY.labels(result="attempts_exceeded").inc()
            raise AttemptsExceeded()
        if not hmac.compare_digest(otp.code_hash, code_hash(phone, code)):
            await limiter.register_failed_verify(phone)
            OTP_VERIFY.labels(result="wrong_code").inc()
            raise InvalidCode("Kod noto'g'ri")
        otp.used_at = now

        user, is_new = await repo.get_or_create_user_by_phone(session, phone, now)
        if not user.is_active:
            OTP_VERIFY.labels(result="blocked").inc()
            raise AccountBlocked()
        if is_new:
            outbox.add_event(
                session,
                Events.USER_REGISTERED,
                aggregate_id=user.id,
                data={"user_id": user.id, "phone": phone, "registered_at": now},
            )
        user.is_phone_verified = True
        user.last_login_at = now
        role_set = await compute_roles(session, user.id)
        pair, _ = await sessions.issue_pair(
            session, user, role_set, role_set.default_active(user.preferred_role), client=client, grant="otp", now=now
        )

    OTP_VERIFY.labels(result="ok").inc()
    logger.info("login", extra={"phone": mask_phone(phone), "is_new": is_new})
    return LoginResult(pair=pair, is_new_user=is_new, phone=phone)
