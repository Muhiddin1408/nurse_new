"""catalog-service biznes xatolari. Topilmadi va begona — bir xil javob (A4)."""

from __future__ import annotations

from medbron_common.errors import BadRequest, Conflict, Forbidden, NotFound, Unprocessable


class DoctorNotFound(NotFound):
    code = "DOCTOR_NOT_FOUND"
    message = "Shifokor topilmadi"


class ClinicNotFound(NotFound):
    code = "CLINIC_NOT_FOUND"
    message = "Klinika topilmadi"


class RoomNotFound(NotFound):
    code = "ROOM_NOT_FOUND"
    message = "Xona topilmadi"


class ServiceNotFound(NotFound):
    code = "SERVICE_NOT_FOUND"
    message = "Xizmat topilmadi"


class SpecializationNotFound(NotFound):
    code = "SPECIALIZATION_NOT_FOUND"
    message = "Mutaxassislik topilmadi"


class AffiliationNotFound(NotFound):
    code = "AFFILIATION_NOT_FOUND"
    message = "Hamkorlik topilmadi"


class DocumentNotFound(NotFound):
    code = "DOCUMENT_NOT_FOUND"
    message = "Hujjat topilmadi"


class NotADoctor(Forbidden):
    code = "NOT_A_DOCTOR"
    message = "Shifokor profili yo'q — avval onboarding'ni boshlang"


class DoctorNotApproved(Forbidden):
    code = "DOCTOR_NOT_APPROVED"
    message = "Bu amal faqat tasdiqlangan shifokor uchun"


class NotClinicAdmin(NotFound):
    """Boshqarmaydigan klinika — xuddi yo'qdek (mavjudligi oshkor qilinmaydi)."""

    code = "CLINIC_NOT_FOUND"
    message = "Klinika topilmadi"


class InvalidTransition(Conflict):
    code = "INVALID_STATE"
    message = "Hozirgi holatda bu amalni bajarib bo'lmaydi"


class NotEditable(Conflict):
    code = "PROFILE_LOCKED"
    message = "Profil faqat qoralama yoki rad etilgan holatda tahrirlanadi"


class SubmitIncomplete(Unprocessable):
    code = "ONBOARDING_INCOMPLETE"
    message = "Arizani yuborish uchun ma'lumot yetarli emas"


class LicenseExpired(Conflict):
    code = "LICENSE_EXPIRED"
    message = "Litsenziya muddati o'tgan"


class AlreadyExists(Conflict):
    code = "ALREADY_EXISTS"
    message = "Allaqachon mavjud"


class LargePriceChange(Conflict):
    code = "LARGE_PRICE_CHANGE"
    message = "Narx 50% dan ko'p oshmoqda — `confirm_large_change: true` bilan tasdiqlang"


class InvalidRequest(BadRequest):
    code = "INVALID_REQUEST"


class InvalidFile(Unprocessable):
    code = "INVALID_FILE"
    message = "Fayl yaroqsiz (yuklanmagan, tekshirilmagan yoki boshqa turdagi)"
