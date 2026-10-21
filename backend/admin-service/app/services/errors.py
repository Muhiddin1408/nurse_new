"""admin-service biznes xatolari."""

from __future__ import annotations

from medbron_common.errors import Conflict, NotFound, ServiceUnavailable, TooManyRequests, Unauthorized, Unprocessable


class InvalidCredentials(Unauthorized):
    """Login yoki parol noto'g'ri — QAYSI biri ekani aytilmaydi."""

    code = "INVALID_CREDENTIALS"
    message = "Login yoki parol noto'g'ri"


class MfaInvalid(Unauthorized):
    code = "MFA_INVALID"
    message = "Tasdiqlash kodi noto'g'ri"


class MfaExpired(Unauthorized):
    code = "MFA_EXPIRED"
    message = "Kirish bosqichi muddati o'tdi. Qaytadan kiring"


class LoginLocked(TooManyRequests):
    code = "LOGIN_LOCKED"
    message = "Juda ko'p muvaffaqiyatsiz urinish. Keyinroq urinib ko'ring"


class LimiterUnavailable(ServiceUnavailable):
    code = "RATE_LIMITER_UNAVAILABLE"
    message = "Xizmat vaqtincha mavjud emas"


class AdminNotFound(NotFound):
    code = "ADMIN_NOT_FOUND"
    message = "Admin topilmadi"


class RoleNotFound(NotFound):
    code = "ROLE_NOT_FOUND"
    message = "Rol topilmadi"


class AlreadyExists(Conflict):
    code = "ALREADY_EXISTS"
    message = "Bunday nom allaqachon mavjud"


class RoleInUse(Conflict):
    code = "ROLE_IN_USE"
    message = "Rol adminlarga biriktirilgan — avval ulardan oling"


class LastAdminManager(Conflict):
    code = "LAST_ADMIN_MANAGER"
    message = "Kamida bitta faol admin `admins.manage` huquqiga ega bo'lishi shart"


class WeakPassword(Unprocessable):
    code = "WEAK_PASSWORD"


class SettingsVersionConflict(Conflict):
    code = "SETTINGS_VERSION_CONFLICT"
    message = "Sozlamalar boshqa admin tomonidan o'zgartirildi — yangilab, qayta urinib ko'ring"


class InvalidSettings(Unprocessable):
    code = "INVALID_SETTINGS"
