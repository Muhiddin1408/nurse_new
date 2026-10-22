"""payment-service biznes xatolari. Begona to'lov/bron — "topilmadi" bilan bir xil (A4)."""

from __future__ import annotations

from medbron_common.errors import BadRequest, Conflict, NotFound


class PaymentNotFound(NotFound):
    code = "PAYMENT_NOT_FOUND"
    message = "To'lov topilmadi"


class BookingNotFound(NotFound):
    code = "BOOKING_NOT_FOUND"
    message = "Bron topilmadi"


class CheckoutNotAllowed(Conflict):
    code = "NOT_PAYABLE"
    message = "Bron to'lov kutish holatida emas"


class RefundNotAllowed(Conflict):
    code = "REFUND_NOT_ALLOWED"


class RefundNotFound(NotFound):
    code = "REFUND_NOT_FOUND"
    message = "Refund topilmadi"


class InvalidRegistry(BadRequest):
    code = "INVALID_REGISTRY"
