"""Event → xabar(lar). Handler FAQAT qator yozadi (xabarlar outbox'i, event bilan bitta tranzaksiyada);
yuborish — dispatcher. Shu sababli provayder sekinlashsa ham event qayta ishlash to'xtamaydi va
takroriy event takroriy SMS bermaydi (`dedupe_key = event:<event_id>:<shablon>:<qabul qiluvchi>`).

Parametrlar XOM saqlanadi (`when` — ISO vaqt, `amount_tiyin`, `place`) — til va format yuborish
paytida qo'llanadi (foydalanuvchi tilini o'zgartirsa, navbatdagi eslatma yangi tilda ketadi).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus import Envelope

from app.core.config import get_settings
from app.repositories import notifications as repo

logger = logging.getLogger(__name__)

REMINDER, REVIEW_REQUEST = "reminder", "review_request"


async def notify_user(
    session: AsyncSession,
    event: Envelope,
    user_id: uuid.UUID | str | None,
    template: str,
    params: dict[str, Any] | None = None,
    *,
    booking_id: uuid.UUID | str | None = None,
) -> None:
    if user_id is None:
        return
    uid = uuid.UUID(str(user_id))
    await repo.enqueue(
        session,
        {
            "audience": "user",
            "user_id": uid,
            "template": template,
            "params": params or {},
            "booking_id": uuid.UUID(str(booking_id)) if booking_id else None,
            "dedupe_key": f"event:{event.event_id}:{template}:{uid}",
            "source_event_id": event.event_id,
        },
    )


async def notify_doctor(
    session: AsyncSession,
    event: Envelope,
    doctor_id: uuid.UUID | str,
    template: str,
    params: dict[str, Any] | None = None,
    *,
    booking_id: uuid.UUID | str | None = None,
) -> None:
    """Shifokorga — uning user kontakti orqali. `doctor_id → user_id` hali ma'lum bo'lmasa (catalog eventi
    kechikkan) — xabar o'tkazib yuboriladi va log qilinadi (bron/payout ma'lumoti ilovada baribir ko'rinadi)."""
    doctor = await repo.get_doctor(session, uuid.UUID(str(doctor_id)))
    if doctor is None:
        logger.warning("doctor contact unknown", extra={"doctor_id": str(doctor_id), "template": template})
        return
    await notify_user(session, event, doctor.user_id, template, params, booking_id=booking_id)


async def notify_ops(session: AsyncSession, event: Envelope, template: str, params: dict[str, Any]) -> None:
    await repo.enqueue(
        session,
        {
            "audience": "ops",
            "user_id": None,
            "template": template,
            "params": params,
            "dedupe_key": f"event:{event.event_id}:{template}:ops",
            "source_event_id": event.event_id,
        },
    )


async def schedule_reminders(session: AsyncSession, data: dict[str, Any], start_at: datetime) -> None:
    """T-24 soat, T-2 soat va qabuldan keyin baholash so'rovi. Idempotent (UNIQUE dedupe_key).
    Vaqti ALLAQACHON o'tgan eslatma yaratilmaydi (qabulga 3 soat qolganda "ertaga qabul" SMS'i ketmasin)."""
    settings = get_settings()
    now = datetime.now(UTC)
    booking_id = uuid.UUID(data["booking_id"])
    params = {"number": data["number"], "when": start_at.isoformat(), "place": data.get("place", "clinic")}
    plan: list[tuple[str, str, int, datetime]] = []
    if settings.reminder_first_hours:
        plan.append(("booking_reminder_24h", REMINDER, 24, start_at - timedelta(hours=settings.reminder_first_hours)))
    if settings.reminder_second_hours:
        plan.append(("booking_reminder_2h", REMINDER, 2, start_at - timedelta(hours=settings.reminder_second_hours)))
    if settings.review_request_after_hours:
        plan.append(
            (
                "booking_review_request",
                REVIEW_REQUEST,
                0,
                start_at + timedelta(hours=settings.review_request_after_hours),
            )
        )
    for template, kind, _, send_at in plan:
        if kind == REMINDER and send_at <= now:
            continue
        await repo.enqueue(
            session,
            {
                "audience": "user",
                "user_id": uuid.UUID(data["client_id"]),
                "template": template,
                "params": params,
                "booking_id": booking_id,
                "kind": kind,
                "send_at": send_at,
                "dedupe_key": f"{template}:{booking_id}",
            },
        )


def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def _booking_projection(session: AsyncSession, event: Envelope, status: str, **extra: Any) -> None:
    data = event.data
    values: dict[str, Any] = {
        "booking_id": uuid.UUID(data["booking_id"]),
        "number": data["number"],
        "client_id": uuid.UUID(data["client_id"]),
        "doctor_id": uuid.UUID(data["doctor_id"]),
        "place": data.get("place", "clinic"),
        "start_at": _iso(data["start_at"]),
        "status": status,
        "changed_at": event.occurred_at,
    }
    values.update(extra)
    await repo.apply_booking(session, values)


# --- user ------------------------------------------------------------------------------------


async def on_user_registered(session: AsyncSession, event: Envelope) -> None:
    await repo.upsert_contact(session, uuid.UUID(event.data["user_id"]), phone=event.data["phone"], is_active=True)


async def on_user_roles_changed(session: AsyncSession, event: Envelope) -> None:
    await repo.upsert_contact(session, uuid.UUID(event.data["user_id"]), roles=list(event.data["roles"]))


async def on_user_profile_updated(session: AsyncSession, event: Envelope) -> None:
    await repo.upsert_contact(
        session,
        uuid.UUID(event.data["user_id"]),
        full_name=event.data["full_name"],
        language=event.data["preferred_language"],
    )


async def on_user_deactivated(session: AsyncSession, event: Envelope) -> None:
    uid = uuid.UUID(event.data["user_id"])
    await repo.upsert_contact(session, uid, is_active=False)
    await repo.cancel_pending(session, user_id=uid, reason="user_deactivated")


async def on_user_reactivated(session: AsyncSession, event: Envelope) -> None:
    await repo.upsert_contact(session, uuid.UUID(event.data["user_id"]), is_active=True)


async def on_user_deletion_requested(session: AsyncSession, event: Envelope) -> None:
    """Kontakt (telefon, ism, Telegram) o'chiriladi — shaxsiy ma'lumot bu servisda qolmaydi."""
    uid = uuid.UUID(event.data["user_id"])
    await repo.upsert_contact(
        session, uid, phone=None, full_name="", telegram_chat_id=None, is_active=False, marketing_opt_in=False
    )
    await repo.drop_user_devices(session, uid)
    await repo.cancel_pending(session, user_id=uid, reason="user_deleted")


# --- doctor / clinic -------------------------------------------------------------------------


async def on_doctor_onboarding_submitted(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await notify_user(
        session, event, data["user_id"], "doctor_onboarding_submitted", {"due": data["moderation_due_at"]}
    )
    await notify_ops(
        session, event, "ops_moderation_new", {"doctor_id": data["doctor_id"], "due": data["moderation_due_at"]}
    )


async def on_doctor_approved(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await repo.upsert_doctor(
        session,
        doctor_id=uuid.UUID(data["doctor_id"]),
        user_id=uuid.UUID(data["user_id"]),
        full_name=data["full_name"],
        changed_at=event.occurred_at,
    )
    await notify_user(session, event, data["user_id"], "doctor_approved")


async def on_doctor_profile_updated(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await repo.upsert_doctor(
        session,
        doctor_id=uuid.UUID(data["doctor_id"]),
        user_id=uuid.UUID(data["user_id"]),
        full_name=data["full_name"],
        changed_at=event.occurred_at,
    )


async def on_doctor_rejected(session: AsyncSession, event: Envelope) -> None:
    await notify_user(session, event, event.data["user_id"], "doctor_rejected", {"reason": event.data["reason"]})


async def on_doctor_suspended(session: AsyncSession, event: Envelope) -> None:
    await notify_user(session, event, event.data["user_id"], "doctor_suspended", {"reason": event.data["reason"]})


async def on_doctor_reinstated(session: AsyncSession, event: Envelope) -> None:
    await notify_user(session, event, event.data["user_id"], "doctor_reinstated")


async def on_doctor_license_expiring(session: AsyncSession, event: Envelope) -> None:
    await notify_user(
        session, event, event.data["user_id"], "doctor_license_expiring", {"date": event.data["license_expires_at"]}
    )


async def on_clinic_member_added(session: AsyncSession, event: Envelope) -> None:
    await notify_user(session, event, event.data["user_id"], "clinic_member_added")


async def on_affiliation_invited(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await repo.upsert_doctor(
        session,
        doctor_id=uuid.UUID(data["doctor_id"]),
        user_id=uuid.UUID(data["doctor_user_id"]),
        full_name=None,
        changed_at=event.occurred_at,
    )
    await notify_user(session, event, data["doctor_user_id"], "doctor_clinic_invite", {"clinic": data["clinic_name"]})


async def on_affiliation_ended(session: AsyncSession, event: Envelope) -> None:
    await notify_doctor(session, event, event.data["doctor_id"], "doctor_affiliation_ended")


# --- booking ---------------------------------------------------------------------------------


async def on_booking_created(session: AsyncSession, event: Envelope) -> None:
    # Mijoz to'lov oynasida — "bron yaratildi" xabari ortiqcha (10 daqiqada tasdiq yoki muddat o'tishi keladi)
    await _booking_projection(session, event, event.data["status"])


async def on_booking_confirmed(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await _booking_projection(session, event, "confirmed")
    params = {"number": data["number"], "when": data["start_at"], "place": data["place"]}
    await notify_user(session, event, data["client_id"], "booking_confirmed", params, booking_id=data["booking_id"])
    # Eslatmalar aynan tasdiqda rejalashtiriladi (to'lanmagan bronlarning ko'pchiligi muddati o'tadi)
    await schedule_reminders(session, data, _iso(data["start_at"]))
    # Shifokor yangi bron haqida bilmasa — tizim ishlamaydi
    await notify_doctor(session, event, data["doctor_id"], "doctor_new_booking", params, booking_id=data["booking_id"])


async def on_booking_cancelled(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    booking_id = uuid.UUID(data["booking_id"])
    await _booking_projection(session, event, "cancelled")
    await repo.cancel_pending(session, booking_id=booking_id, reason="booking_cancelled")
    await notify_user(session, event, data["client_id"], "booking_cancelled", {"number": data["number"]})
    # To'lanmagan bron bekor bo'lsa shifokorni bezovta qilmaymiz — u unga hech qachon ko'rinmagan
    if data.get("previous_status") in ("confirmed", "in_progress"):
        await notify_doctor(
            session,
            event,
            data["doctor_id"],
            "doctor_booking_cancelled",
            {"number": data["number"], "when": data["start_at"]},
        )


async def on_booking_expired(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await _booking_projection(session, event, "expired")
    await repo.cancel_pending(session, booking_id=uuid.UUID(data["booking_id"]), reason="booking_expired")
    await notify_user(session, event, data["client_id"], "booking_expired", {"number": data["number"]})


async def on_booking_rescheduled(session: AsyncSession, event: Envelope) -> None:
    """Yangi vaqt — mijozga, shifokorga va eslatmalarga (aks holda mijoz ESKI vaqt haqida eslatma oladi)."""
    data = event.data
    booking_id = uuid.UUID(data["booking_id"])
    existing = await repo.get_booking(session, booking_id)
    if existing is not None:
        existing.start_at = _iso(data["new_start_at"])
        existing.changed_at = max(existing.changed_at, event.occurred_at)
        place = existing.place
    else:
        place = "clinic"
    await repo.delete_unsent_reminders(session, booking_id)
    payload = {
        "booking_id": data["booking_id"],
        "number": data["number"],
        "client_id": data["client_id"],
        "place": place,
    }
    await schedule_reminders(session, payload, _iso(data["new_start_at"]))
    params = {"number": data["number"], "when": data["new_start_at"]}
    await notify_user(session, event, data["client_id"], "booking_rescheduled", params)
    await notify_doctor(session, event, data["doctor_id"], "doctor_booking_rescheduled", params)


async def on_booking_completed(session: AsyncSession, event: Envelope) -> None:
    await _booking_projection(session, event, "completed")


async def on_booking_no_show(session: AsyncSession, event: Envelope) -> None:
    await _booking_projection(session, event, "no_show")


async def on_booking_confirmation_failed(session: AsyncSession, event: Envelope) -> None:
    """Pul yechildi, lekin bron tasdiqlanmadi — mijozga tushuntirish va ops'ga signal (refund kuzatilsin)."""
    data = event.data
    await notify_user(session, event, data["client_id"], "payment_booking_unavailable")
    await notify_ops(
        session,
        event,
        "ops_confirmation_failed",
        {"booking_id": data["booking_id"], "payment_id": data["payment_id"], "amount_tiyin": data["amount_tiyin"],
         "reason": data["reason"]},
    )  # fmt: skip


async def on_waitlist_slot_offered(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    doctor = await repo.get_doctor(session, uuid.UUID(data["doctor_id"]))
    minutes = max(1, int((_iso(data["offer_expires_at"]) - event.occurred_at).total_seconds() // 60))
    params = {"doctor": doctor.full_name if doctor and doctor.full_name else "shifokor", "when": data["start_at"],
              "minutes": minutes}  # fmt: skip
    await notify_user(session, event, data["client_id"], "waitlist_slot_available", params)


# --- pul -------------------------------------------------------------------------------------


async def _number(session: AsyncSession, booking_id: str) -> str:
    booking = await repo.get_booking(session, uuid.UUID(booking_id))
    return booking.number if booking else "—"


async def on_payment_reversed(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    params = {"number": await _number(session, data["booking_id"]), "amount_tiyin": data["amount_tiyin"]}
    await notify_user(session, event, data["client_id"], "payment_reversed", params)


async def on_refund_succeeded(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    params = {"number": await _number(session, data["booking_id"]), "amount_tiyin": data["amount_tiyin"]}
    await notify_user(session, event, data["client_id"], "refund_succeeded", params)


async def on_refund_failed(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await notify_ops(
        session,
        event,
        "ops_refund_failed",
        {"refund_id": data["refund_id"], "amount_tiyin": data["amount_tiyin"], "attempts": data["attempts"],
         "error": data["last_error"][:200]},
    )  # fmt: skip


async def on_payment_discrepancy(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await notify_ops(
        session,
        event,
        "ops_discrepancy",
        {
            "day": data["day"],
            "provider": data["provider"] or "—",  # ichki tekshiruv (ledger) — provayderga bog'liq emas
            "type": data["type"],
            "payment_id": data["payment_id"] or "—",
        },
    )


async def on_payout_calculated(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    params = {"period": f"{data['period_start']} — {data['period_end']}", "amount_tiyin": data["net_tiyin"]}
    await notify_doctor(session, event, data["doctor_id"], "doctor_payout_calculated", params)


async def on_payout_paid(session: AsyncSession, event: Envelope) -> None:
    await notify_doctor(
        session, event, event.data["doctor_id"], "doctor_payout_paid", {"amount_tiyin": event.data["net_tiyin"]}
    )


# --- sharh / nizo ----------------------------------------------------------------------------


async def on_review_published(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    params = {"rating": data["rating"], "number": await _number(session, data["booking_id"])}
    if data.get("doctor_user_id"):
        await notify_user(session, event, data["doctor_user_id"], "doctor_new_review", params)
    else:
        await notify_doctor(session, event, data["doctor_id"], "doctor_new_review", params)


async def on_review_rejected(session: AsyncSession, event: Envelope) -> None:
    await notify_user(session, event, event.data["client_id"], "review_rejected", {"reason": event.data["reason"]})


async def on_dispute_opened(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await notify_ops(
        session,
        event,
        "ops_dispute_opened",
        {"dispute_id": data["dispute_id"], "booking_id": data["booking_id"], "reason": data["reason"][:200],
         "due": data["due_at"]},
    )  # fmt: skip


async def on_dispute_resolved(session: AsyncSession, event: Envelope) -> None:
    data = event.data
    await notify_user(session, event, data["raised_by"], "dispute_resolved", {"amount_tiyin": data["refund_tiyin"]})


async def on_dispute_sla_breached(session: AsyncSession, event: Envelope) -> None:
    await notify_ops(
        session,
        event,
        "ops_dispute_sla_breached",
        {"dispute_id": event.data["dispute_id"], "due": event.data["due_at"]},
    )
