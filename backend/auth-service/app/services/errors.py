"""auth-service biznes xatolari (yagona format: `{"detail": {"code", "message"}}`)."""

from __future__ import annotations

from medbron_common.errors import BadRequest, Conflict, Forbidden, NotFound, ServiceUnavailable, Unauthorized
from medbron_common.errors import TooManyRequests as _TooManyRequests


class InvalidPhoneError(BadRequest):
    code = "INVALID_PHONE"
    message = "Faqat O'zbekiston raqamlari (+998XXXXXXXXX) qabul qilinadi"


class InvalidCode(BadRequest):
    code = "INVALID_CODE"
    message = "Kod noto'g'ri yoki muddati o'tgan"


class AttemptsExceeded(BadRequest):
    code = "OTP_ATTEMPTS_EXCEEDED"
    message = "Urinishlar soni tugadi. Yangi kod so'rang"


class TooManyRequests(_TooManyRequests):
    pass


class SmsUnavailable(ServiceUnavailable):
    """OTP uchun FAIL-CLOSED: kod yetmasa "yuborildi" deb yolg'on aytmaymiz."""

    code = "SMS_UNAVAILABLE"
    message = "SMS yuborib bo'lmadi. Birozdan keyin urinib ko'ring"


class RateLimiterUnavailable(ServiceUnavailable):
    """Rate-limit — xavfsizlik nazorati: Redis ishlamasa OTP berilmaydi (fail-closed)."""

    code = "RATE_LIMITER_UNAVAILABLE"
    message = "Xizmat vaqtincha mavjud emas. Birozdan keyin urinib ko'ring"


class AccountBlocked(Forbidden):
    code = "ACCOUNT_BLOCKED"
    message = "Akkaunt bloklangan"


class RefreshInvalid(Unauthorized):
    code = "REFRESH_INVALID"
    message = "Sessiya yaroqsiz yoki muddati o'tgan. Qayta kiring"


class RefreshReused(Unauthorized):
    code = "REFRESH_REUSED"
    message = "Sessiya xavfsizlik sababli yopildi. Qayta kiring"


class RoleNotAvailable(Forbidden):
    code = "ROLE_NOT_AVAILABLE"
    message = "Bu rol sizga berilmagan"


class UserNotFound(NotFound):
    code = "USER_NOT_FOUND"
    message = "Foydalanuvchi topilmadi"


class InvalidStateTransition(Conflict):
    code = "INVALID_STATE"
