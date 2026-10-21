from __future__ import annotations

from medbron_common.errors import Conflict, NotFound, Unprocessable


class ServicesUnavailable(Unprocessable):
    code = "SERVICES_UNAVAILABLE"
    message = "Xizmatlar topilmadi, faol emas yoki bu shifokorga tegishli emas"


class DoctorNotBookable(Unprocessable):
    code = "DOCTOR_NOT_BOOKABLE"
    message = "Shifokor hozircha bron qabul qilmaydi"


class PromoInvalidError(Unprocessable):
    code = "PROMO_INVALID"


class QuoteExpired(NotFound):
    code = "QUOTE_EXPIRED"
    message = "Narx hisobi muddati o'tgan — qaytadan hisoblang"


class PromoLimitReached(Conflict):
    code = "PROMO_LIMIT_REACHED"
    message = "Promo kod limiti tugadi"


class PackageExhausted(Conflict):
    code = "PACKAGE_EXHAUSTED"
    message = "Paket seanslari tugagan yoki paket faol emas"


class PackageNotFound(NotFound):
    code = "PACKAGE_NOT_FOUND"
    message = "Paket topilmadi yoki faol emas"


class PackageAlreadyActive(Conflict):
    code = "PACKAGE_ALREADY_ACTIVE"
    message = "Bu xizmat bo'yicha faol paketingiz allaqachon bor"


class PromoNotFound(NotFound):
    code = "PROMO_NOT_FOUND"
    message = "Promo kod topilmadi"


class PromoCodeExists(Conflict):
    code = "PROMO_CODE_EXISTS"
    message = "Bunday promo kod allaqachon mavjud"


class InvalidPromoTerms(Unprocessable):
    code = "INVALID_PROMO"


class ServiceNotOwned(Unprocessable):
    code = "SERVICE_NOT_OWNED"
    message = "Xizmat sizga tegishli emas yoki faol emas"
