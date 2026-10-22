"""booking-service biznes xatolari. Begona bron/bemor/manzil — "topilmadi" bilan bir xil (A4)."""

from __future__ import annotations

from medbron_common.errors import BadRequest, Conflict, NotFound, Unprocessable


class BookingNotFound(NotFound):
    code = "BOOKING_NOT_FOUND"
    message = "Bron topilmadi"


class InvalidBookingRequest(BadRequest):
    code = "INVALID_BOOKING_REQUEST"


class DoctorNotBookable(BadRequest):
    code = "DOCTOR_NOT_BOOKABLE"
    message = "Shifokor hozircha bron qabul qilmaydi"


class SlotTaken(Conflict):
    code = "SLOT_TAKEN"
    message = "Bu vaqt band qilingan — boshqa vaqtni tanlang"


class BookingConflict(Conflict):
    code = "BOOKING_CONFLICT"


class TooManyPending(Conflict):
    code = "TOO_MANY_PENDING"
    message = "Avval oldingi bronlarni to'lang yoki bekor qiling"


class IdempotencyMismatch(Unprocessable):
    code = "IDEMPOTENCY_KEY_REUSED"
    message = "Idempotency-Key boshqa so'rov bilan ishlatilgan"


class IdempotencyInProgress(Conflict):
    code = "IDEMPOTENCY_IN_PROGRESS"
    message = "Shu kalit bilan so'rov hali bajarilmoqda"


class PromoInvalid(BadRequest):
    code = "PROMO_INVALID"


class InvalidTransition(Conflict):
    code = "INVALID_STATE"
