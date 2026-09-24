"""booking/disputes.py — nizolar oqimi (B11)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from api import audit
from django.utils import timezone

from api.booking.services import BookingError, InvalidBookingRequest
from api.payments import refunds as refund_services
from apps.booking.models import Booking, Dispute

DISPUTE_SLA = timedelta(hours=48)
DISPUTABLE_STATUSES = (Booking.Status.CONFIRMED, Booking.Status.COMPLETED, Booking.Status.NO_SHOW)


def open_dispute(*, booking: Booking, user, reason: str, description: str = "") -> Dispute:
    if booking.status not in DISPUTABLE_STATUSES:
        raise BookingError("Bu bron bo'yicha nizo ochib bo'lmaydi")
    try:
        with transaction.atomic():
            return Dispute.objects.create(
                booking=booking, raised_by=user, reason=reason,
                description=description, due_at=timezone.now() + DISPUTE_SLA,
            )
    except IntegrityError:
        raise BookingError("Bu bron bo'yicha ochiq nizo allaqachon bor")


def resolve_dispute(
    dispute_id: UUID, *, in_favor_of: str, note: str, admin, refund_amount: Decimal = Decimal("0")
) -> Dispute:
    if in_favor_of not in ("client", "doctor"):
        raise InvalidBookingRequest("in_favor_of: client yoki doctor")
    if in_favor_of == "doctor" and refund_amount > 0:
        raise InvalidBookingRequest("Shifokor foydasiga hal qilinganda refund bo'lmaydi")

    with transaction.atomic():
        dispute = Dispute.objects.select_for_update().get(id=dispute_id)
        if dispute.status != Dispute.Status.OPEN:
            return dispute  # idempotent

        refund = None
        if refund_amount > 0:
            refund = refund_services.request_refund(
                booking_id=dispute.booking_id, amount=refund_amount,
                reason=f"dispute:{dispute.id}", initiated_by="admin",
            )
            if refund is None:
                raise BookingError("Qaytariladigan summa yo'q")

        Dispute.objects.filter(id=dispute.id).update(
            status=Dispute.Status.RESOLVED_CLIENT if in_favor_of == "client" else Dispute.Status.RESOLVED_DOCTOR,
            resolution_note=note, resolved_by=admin, resolved_at=timezone.now(),
            refund=refund, updated_at=timezone.now(),
        )
        audit.record("dispute.resolve", obj=dispute, actor=admin, before={"status": dispute.status},
                     after={"in_favor_of": in_favor_of, "refund_amount": refund_amount, "note": note})
    dispute.refresh_from_db()
    return dispute
