"""Event → xabarlar outbox'i va proyeksiyalar (haqiqiy Postgres). Docker talab qiladi."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from medbron_common.events import CATALOG
from medbron_common.testing import sample_data

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


def notifications(db, **where: str) -> list[dict]:
    rows = db.all(
        "SELECT template, user_id::text, status, kind, send_at, params, audience FROM notifications "
        "ORDER BY created_at, template"
    )
    out = [dict(zip(("template", "user_id", "status", "kind", "send_at", "params", "audience"), r, strict=True))
           for r in rows]  # fmt: skip
    return [n for n in out if all(n[k] == v for k, v in where.items())]


def setup_people(run_handler) -> tuple[str, str, str]:
    client, doctor_user, doctor = (str(uuid.uuid4()) for _ in range(3))
    run_handler("user.registered", sample_data("user.registered", user_id=client, phone="+998901112233"))
    run_handler("user.registered", sample_data("user.registered", user_id=doctor_user, phone="+998935556677"))
    run_handler(
        "doctor.approved", sample_data("doctor.approved", doctor_id=doctor, user_id=doctor_user, full_name="Dr. Aliyev")
    )
    return client, doctor_user, doctor


def test_every_catalog_consume_has_handler() -> None:
    from medbron_common.eventbus import EventConsumer

    from app.models import ProcessedEvent
    from app.workers import handlers

    consumer = EventConsumer(
        service_name="notification-service", amqp_url="amqp://x/", session_factory=None, processed_model=ProcessedEvent
    )
    handlers.register(consumer)
    assert consumer.routing_keys == sorted(e for e, s in CATALOG.items() if "notification-service" in s.consumers)


def test_contact_projection(db, run_handler) -> None:
    uid = str(uuid.uuid4())
    run_handler("user.registered", sample_data("user.registered", user_id=uid, phone="+998901112233"))
    run_handler("user.profile_updated", sample_data("user.profile_updated", user_id=uid, full_name="Ali",
                                                    preferred_language="ru"))  # fmt: skip
    run_handler("user.roles_changed", sample_data("user.roles_changed", user_id=uid, roles=["client", "doctor"]))
    row = db.all("SELECT phone, full_name, language, roles, is_active FROM contacts WHERE user_id = :u", u=uid)[0]
    assert tuple(row) == ("+998901112233", "Ali", "ru", ["client", "doctor"], True)
    run_handler("user.deactivated", sample_data("user.deactivated", user_id=uid))
    assert db.scalar("SELECT is_active FROM contacts WHERE user_id = :u", u=uid) is False
    run_handler("user.reactivated", sample_data("user.reactivated", user_id=uid))
    assert db.scalar("SELECT is_active FROM contacts WHERE user_id = :u", u=uid) is True


def test_booking_confirmed_schedules_everything_once(db, run_handler) -> None:
    client, doctor_user, doctor = setup_people(run_handler)
    booking = str(uuid.uuid4())
    start = (NOW + timedelta(days=3)).replace(microsecond=0)
    data = sample_data(
        "booking.confirmed", booking_id=booking, client_id=client, doctor_id=doctor, number="MB-1",
        start_at=start.isoformat().replace("+00:00", "Z"), place="home",
    )  # fmt: skip
    raw = run_handler("booking.confirmed", data)
    # Takror yetkazilgan event (boshqa event_id, lekin xuddi shu bron) — eslatmalar takrorlanmaydi
    run_handler("booking.confirmed", data)

    reminders = notifications(db, kind="reminder")
    assert sorted(n["template"] for n in reminders) == ["booking_reminder_24h", "booking_reminder_2h"]
    assert {n["send_at"] for n in reminders} == {start - timedelta(hours=24), start - timedelta(hours=2)}
    assert [n["send_at"] for n in notifications(db, kind="review_request")] == [start + timedelta(hours=2)]
    assert notifications(db, template="doctor_new_booking")[0]["user_id"] == doctor_user
    confirmed = notifications(db, template="booking_confirmed")
    assert confirmed[0]["params"] == {"number": "MB-1", "when": data["start_at"], "place": "home"}
    assert db.scalar("SELECT status FROM booking_projection WHERE booking_id = :b", b=booking) == "confirmed"

    # Aynan bir xil event (event_id) qayta qo'llansa ham — bitta xabar (dedupe_key)
    from medbron_common.eventbus import Envelope

    from app.services import rules

    async def again(session):
        await rules.on_booking_confirmed(session, Envelope.model_validate(raw))

    db.run(again)
    assert len(notifications(db, template="doctor_new_booking")) == 2  # ikki xil event — ikki xabar
    assert len(notifications(db, kind="reminder")) == 2


def test_reminders_skip_past_and_cancel_on_cancellation(db, run_handler) -> None:
    client, _, doctor = setup_people(run_handler)
    booking = str(uuid.uuid4())
    soon = (NOW + timedelta(hours=3)).isoformat()
    run_handler("booking.confirmed", sample_data("booking.confirmed", booking_id=booking, client_id=client,
                                                 doctor_id=doctor, start_at=soon))  # fmt: skip
    # T-24 vaqti o'tib ketgan — yaratilmaydi; T-2 bor
    assert [n["template"] for n in notifications(db, kind="reminder")] == ["booking_reminder_2h"]

    run_handler(
        "booking.cancelled",
        sample_data("booking.cancelled", booking_id=booking, client_id=client, doctor_id=doctor, start_at=soon,
                    previous_status="confirmed"),
    )  # fmt: skip
    assert {n["status"] for n in notifications(db, kind="reminder")} == {"cancelled"}
    assert notifications(db, template="booking_cancelled")[0]["user_id"] == client
    assert len(notifications(db, template="doctor_booking_cancelled")) == 1
    assert db.scalar("SELECT status FROM booking_projection WHERE booking_id = :b", b=booking) == "cancelled"


def test_unpaid_cancel_does_not_bother_doctor_and_expired_text(db, run_handler) -> None:
    client, _, doctor = setup_people(run_handler)
    run_handler("booking.cancelled", sample_data("booking.cancelled", client_id=client, doctor_id=doctor,
                                                 previous_status="pending_payment"))  # fmt: skip
    assert notifications(db, template="doctor_booking_cancelled") == []
    run_handler("booking.expired", sample_data("booking.expired", client_id=client, doctor_id=doctor))
    assert len(notifications(db, template="booking_expired")) == 1


def test_reschedule_moves_reminders(db, run_handler) -> None:
    client, _, doctor = setup_people(run_handler)
    booking = str(uuid.uuid4())
    old = NOW + timedelta(days=2)
    new = NOW + timedelta(days=5)
    run_handler("booking.confirmed", sample_data("booking.confirmed", booking_id=booking, client_id=client,
                                                 doctor_id=doctor, start_at=old.isoformat()))  # fmt: skip
    run_handler(
        "booking.rescheduled",
        sample_data("booking.rescheduled", booking_id=booking, client_id=client, doctor_id=doctor,
                    new_start_at=new.isoformat(), old_start_at=old.isoformat()),
    )  # fmt: skip
    sends = sorted(n["send_at"] for n in notifications(db, kind="reminder"))
    assert sends == [new - timedelta(hours=24), new - timedelta(hours=2)]
    assert len(notifications(db, template="booking_rescheduled")) == 1
    assert len(notifications(db, template="doctor_booking_rescheduled")) == 1
    start = db.scalar("SELECT start_at FROM booking_projection WHERE booking_id = :b", b=booking)
    assert abs((start - new).total_seconds()) < 1


def test_money_doctor_review_dispute_and_ops(db, run_handler) -> None:
    client, doctor_user, doctor = setup_people(run_handler)
    booking = str(uuid.uuid4())
    run_handler("booking.created", sample_data("booking.created", booking_id=booking, client_id=client,
                                               doctor_id=doctor, number="MB-7"))  # fmt: skip
    run_handler("refund.succeeded", sample_data("refund.succeeded", booking_id=booking, client_id=client,
                                                amount_tiyin=12_500_000))  # fmt: skip
    assert notifications(db, template="refund_succeeded")[0]["params"] == {"number": "MB-7", "amount_tiyin": 12_500_000}
    run_handler("payment.reversed", sample_data("payment.reversed", booking_id=booking, client_id=client))
    run_handler("payout.paid", sample_data("payout.paid", doctor_id=doctor, net_tiyin=100))
    assert notifications(db, template="doctor_payout_paid")[0]["user_id"] == doctor_user
    run_handler("payout.calculated", sample_data("payout.calculated", doctor_id=doctor))
    run_handler("review.published", sample_data("review.published", booking_id=booking, doctor_id=doctor,
                                                doctor_user_id=None, rating=5))  # fmt: skip
    assert notifications(db, template="doctor_new_review")[0]["params"] == {"rating": 5, "number": "MB-7"}
    run_handler("review.rejected", sample_data("review.rejected", client_id=client, reason="so'kinish"))
    run_handler("dispute.resolved", sample_data("dispute.resolved", raised_by=client, refund_tiyin=500))
    for event_type in ("refund.failed", "payment.discrepancy_found", "dispute.opened", "dispute.sla_breached"):
        run_handler(event_type, sample_data(event_type))
    run_handler("booking.confirmation_failed", sample_data("booking.confirmation_failed", client_id=client))
    ops = [n["template"] for n in notifications(db, audience="ops")]
    assert sorted(ops) == sorted(
        ["ops_refund_failed", "ops_discrepancy", "ops_dispute_opened", "ops_dispute_sla_breached",
         "ops_confirmation_failed"]
    )  # fmt: skip
    assert len(notifications(db, template="payment_booking_unavailable")) == 1
    # Noma'lum shifokor — xabar yo'q (catalog eventi kechikkan), xato ham yo'q
    run_handler("payout.paid", sample_data("payout.paid", doctor_id=str(uuid.uuid4())))
    assert len(notifications(db, template="doctor_payout_paid")) == 1


def test_doctor_and_clinic_events(db, run_handler) -> None:
    client, doctor_user, doctor = setup_people(run_handler)
    run_handler("doctor.onboarding_submitted", sample_data("doctor.onboarding_submitted", user_id=doctor_user,
                                                           doctor_id=doctor))  # fmt: skip
    for event_type in ("doctor.rejected", "doctor.suspended", "doctor.reinstated", "doctor.license_expiring"):
        run_handler(event_type, sample_data(event_type, user_id=doctor_user, doctor_id=doctor))
    run_handler("doctor.profile_updated", sample_data("doctor.profile_updated", user_id=doctor_user, doctor_id=doctor,
                                                      full_name="Dr. Yangi"))  # fmt: skip
    assert db.scalar("SELECT full_name FROM doctor_contacts WHERE doctor_id = :d", d=doctor) == "Dr. Yangi"
    run_handler("clinic.member_added", sample_data("clinic.member_added", user_id=client))
    new_doc = str(uuid.uuid4())
    run_handler("affiliation.invited", sample_data("affiliation.invited", doctor_id=new_doc, doctor_user_id=client,
                                                   clinic_name="Shifo"))  # fmt: skip
    assert notifications(db, template="doctor_clinic_invite")[0]["params"] == {"clinic": "Shifo"}
    run_handler("affiliation.ended", sample_data("affiliation.ended", doctor_id=new_doc))
    assert notifications(db, template="doctor_affiliation_ended")[0]["user_id"] == client
    templates_sent = {n["template"] for n in notifications(db, user_id=doctor_user)}
    assert {"doctor_approved", "doctor_onboarding_submitted", "doctor_rejected", "doctor_suspended",
            "doctor_reinstated", "doctor_license_expiring"} <= templates_sent  # fmt: skip

    run_handler("waitlist.slot_offered", sample_data(
        "waitlist.slot_offered", client_id=client, doctor_id=doctor,
        offer_expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
    ))  # fmt: skip
    params = notifications(db, template="waitlist_slot_available")[0]["params"]
    assert params["doctor"] == "Dr. Yangi" and 14 <= params["minutes"] <= 15


def test_deletion_wipes_contact_and_cancels_pending(db, run_handler) -> None:
    client, _, doctor = setup_people(run_handler)
    run_handler("booking.confirmed", sample_data("booking.confirmed", client_id=client, doctor_id=doctor,
                                                 start_at=(NOW + timedelta(days=3)).isoformat()))  # fmt: skip
    db.scalar("UPDATE contacts SET telegram_chat_id = 42 WHERE user_id = :u", u=client)
    db.scalar(
        "INSERT INTO push_devices (id, user_id, token, platform) VALUES (:i, :u, 'tok-1234567890', 'android')",
        i=str(uuid.uuid4()),
        u=client,
    )
    run_handler("user.deletion_requested", sample_data("user.deletion_requested", user_id=client))
    row = db.all("SELECT phone, full_name, telegram_chat_id, is_active FROM contacts WHERE user_id = :u", u=client)[0]
    assert tuple(row) == (None, "", None, False)
    assert db.scalar("SELECT count(*) FROM push_devices WHERE user_id = :u", u=client) == 0
    assert {n["status"] for n in notifications(db, user_id=client)} == {"cancelled"}
