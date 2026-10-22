"""Event handlerlar — har biri `app/services/rules.py` dagi qoida. Handler faqat proyeksiya va xabarlar
outbox'iga yozadi (consumer tranzaksiyasi ichida); yuborish — dispatcher. Commit/rollback qilmang.

Katalogda shu servis consumer deb yozilgan HAR event uchun handler bo'lishi shart
(`scripts/check-contracts.py`) — shuning uchun ro'yxat ochiq yozilgan.
"""

from __future__ import annotations

from medbron_common.eventbus import EventConsumer
from medbron_common.events import Events

from app.services import rules


def register(consumer: EventConsumer) -> None:
    consumer.on(Events.USER_REGISTERED)(rules.on_user_registered)
    consumer.on(Events.USER_ROLES_CHANGED)(rules.on_user_roles_changed)
    consumer.on(Events.USER_PROFILE_UPDATED)(rules.on_user_profile_updated)
    consumer.on(Events.USER_DEACTIVATED)(rules.on_user_deactivated)
    consumer.on(Events.USER_REACTIVATED)(rules.on_user_reactivated)
    consumer.on(Events.USER_DELETION_REQUESTED)(rules.on_user_deletion_requested)
    consumer.on(Events.DOCTOR_ONBOARDING_SUBMITTED)(rules.on_doctor_onboarding_submitted)
    consumer.on(Events.DOCTOR_APPROVED)(rules.on_doctor_approved)
    consumer.on(Events.DOCTOR_PROFILE_UPDATED)(rules.on_doctor_profile_updated)
    consumer.on(Events.DOCTOR_REJECTED)(rules.on_doctor_rejected)
    consumer.on(Events.DOCTOR_SUSPENDED)(rules.on_doctor_suspended)
    consumer.on(Events.DOCTOR_REINSTATED)(rules.on_doctor_reinstated)
    consumer.on(Events.DOCTOR_LICENSE_EXPIRING)(rules.on_doctor_license_expiring)
    consumer.on(Events.CLINIC_MEMBER_ADDED)(rules.on_clinic_member_added)
    consumer.on(Events.AFFILIATION_INVITED)(rules.on_affiliation_invited)
    consumer.on(Events.AFFILIATION_ENDED)(rules.on_affiliation_ended)
    consumer.on(Events.BOOKING_CREATED)(rules.on_booking_created)
    consumer.on(Events.BOOKING_CONFIRMED)(rules.on_booking_confirmed)
    consumer.on(Events.BOOKING_CANCELLED)(rules.on_booking_cancelled)
    consumer.on(Events.BOOKING_EXPIRED)(rules.on_booking_expired)
    consumer.on(Events.BOOKING_RESCHEDULED)(rules.on_booking_rescheduled)
    consumer.on(Events.BOOKING_COMPLETED)(rules.on_booking_completed)
    consumer.on(Events.BOOKING_NO_SHOW)(rules.on_booking_no_show)
    consumer.on(Events.BOOKING_CONFIRMATION_FAILED)(rules.on_booking_confirmation_failed)
    consumer.on(Events.WAITLIST_SLOT_OFFERED)(rules.on_waitlist_slot_offered)
    consumer.on(Events.PAYMENT_REVERSED)(rules.on_payment_reversed)
    consumer.on(Events.REFUND_SUCCEEDED)(rules.on_refund_succeeded)
    consumer.on(Events.REFUND_FAILED)(rules.on_refund_failed)
    consumer.on(Events.PAYMENT_DISCREPANCY_FOUND)(rules.on_payment_discrepancy)
    consumer.on(Events.PAYOUT_CALCULATED)(rules.on_payout_calculated)
    consumer.on(Events.PAYOUT_PAID)(rules.on_payout_paid)
    consumer.on(Events.REVIEW_PUBLISHED)(rules.on_review_published)
    consumer.on(Events.REVIEW_REJECTED)(rules.on_review_rejected)
    consumer.on(Events.DISPUTE_OPENED)(rules.on_dispute_opened)
    consumer.on(Events.DISPUTE_RESOLVED)(rules.on_dispute_resolved)
    consumer.on(Events.DISPUTE_SLA_BREACHED)(rules.on_dispute_sla_breached)
