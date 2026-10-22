"""review-service biznes xatolari. Begona bron/sharh — "topilmadi" bilan bir xil (A4)."""

from __future__ import annotations

from medbron_common.errors import BadRequest, Conflict, NotFound


class ReviewNotFound(NotFound):
    code = "REVIEW_NOT_FOUND"
    message = "Sharh topilmadi"


class BookingNotFound(NotFound):
    code = "BOOKING_NOT_FOUND"
    message = "Bron topilmadi"


class NotReviewable(Conflict):
    code = "NOT_REVIEWABLE"
    message = "Faqat yakunlangan qabulga sharh yozish mumkin"


class AlreadyReviewed(Conflict):
    code = "ALREADY_REVIEWED"
    message = "Bu qabulga sharh allaqachon yozilgan"


class InvalidReply(BadRequest):
    code = "INVALID_REPLY"


class InvalidTransition(Conflict):
    code = "INVALID_STATE"
