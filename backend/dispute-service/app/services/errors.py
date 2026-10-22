"""dispute-service biznes xatolari. Begona nizo/bron — "topilmadi" bilan bir xil (A4)."""

from __future__ import annotations

from medbron_common.errors import BadRequest, Conflict, NotFound


class DisputeNotFound(NotFound):
    code = "DISPUTE_NOT_FOUND"
    message = "Nizo topilmadi"


class BookingNotFound(NotFound):
    code = "BOOKING_NOT_FOUND"
    message = "Bron topilmadi"


class NotDisputable(Conflict):
    code = "NOT_DISPUTABLE"
    message = "Bu bron bo'yicha nizo ochib bo'lmaydi"


class DisputeExists(Conflict):
    code = "DISPUTE_EXISTS"
    message = "Bu bron bo'yicha nizo allaqachon ochilgan"


class InvalidState(Conflict):
    code = "INVALID_STATE"


class InvalidResolution(BadRequest):
    code = "INVALID_RESOLUTION"


class EvidenceRejected(BadRequest):
    code = "EVIDENCE_REJECTED"
    message = "Fayl biriktirilmadi: avval yuklang (purpose=dispute_evidence) va yakunlang"


class TooManyEvidence(Conflict):
    code = "TOO_MANY_EVIDENCE"
