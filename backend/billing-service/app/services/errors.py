"""billing-service biznes xatolari. Begona payout / klinika — "topilmadi" bilan bir xil (A4)."""

from __future__ import annotations

from medbron_common.errors import BadRequest, Conflict, NotFound


class PayoutNotFound(NotFound):
    code = "PAYOUT_NOT_FOUND"
    message = "Payout topilmadi"


class ClinicNotFound(NotFound):
    code = "CLINIC_NOT_FOUND"
    message = "Klinika topilmadi"


class InvalidPeriod(BadRequest):
    code = "INVALID_PERIOD"
    message = "period: today|week|month yoki from/to (max 1 yil)"


class PayoutAccountMissing(NotFound):
    code = "PAYOUT_ACCOUNT_NOT_FOUND"
    message = "To'lov rekvizitlari kiritilmagan"


class PayoutConflict(Conflict):
    code = "PAYOUT_CONFLICT"
