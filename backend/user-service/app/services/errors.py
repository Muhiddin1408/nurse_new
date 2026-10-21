from __future__ import annotations

from medbron_common.errors import Conflict, NotFound, Unprocessable


class PatientNotFound(NotFound):
    code = "PATIENT_NOT_FOUND"
    message = "Bemor topilmadi"


class AddressNotFound(NotFound):
    code = "ADDRESS_NOT_FOUND"
    message = "Manzil topilmadi"


class ProfileDeleted(NotFound):
    code = "ACCOUNT_DELETED"
    message = "Akkaunt o'chirilgan"


class ActiveBookingsExist(Conflict):
    code = "ACTIVE_BOOKINGS_EXIST"
    message = "Avval faol bronlarni bekor qiling yoki qabul yakunlanishini kuting"


class LimitReached(Conflict):
    code = "LIMIT_REACHED"


class DoctorAccountDeletion(Conflict):
    code = "DOCTOR_ACCOUNT"
    message = "Shifokor akkaunti qo'llab-quvvatlash xizmati orqali o'chiriladi"


class InvalidAvatar(Unprocessable):
    code = "INVALID_AVATAR"
    message = "Avatar fayli yaroqsiz (yuklanmagan, tekshirilmagan yoki boshqa turdagi)"
