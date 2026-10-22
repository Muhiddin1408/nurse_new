"""Eventlar → daromad daftari: idempotentlik, tartibsiz kelish, zaklad, jarima (ADR-0012)."""

from __future__ import annotations

import uuid

import pytest

from medbron_common.events import Events

from tests import builders as b

pytestmark = pytest.mark.integration


def entries(db, booking_id: str) -> list[tuple]:
    return db.all(
        "SELECT kind, amount_tiyin, gross_tiyin, platform_tiyin, provider_tiyin, doctor_id "
        "FROM earning_entries WHERE booking_id = :b ORDER BY kind",
        b=booking_id,
    )


def test_completed_booking_earns_doctor_share_once(db, run_handler) -> None:
    data = b.completed()
    run_handler(Events.BOOKING_COMPLETED, data)
    run_handler(Events.BOOKING_COMPLETED, data)  # takroriy event — ikkinchi yozuv yo'q
    assert entries(db, data["booking_id"]) == [("earning", 12_600_000, 15_000_000, 2_250_000, 150_000, b.DOCTOR)]
    services = db.scalar("SELECT services FROM earning_entries WHERE booking_id = :b", b=data["booking_id"])
    assert services == ["Konsultatsiya"]


def test_deposit_booking_is_prorated(db, run_handler) -> None:
    data = b.completed(paid=3_000_000)  # 20% zaklad, qolgani klinikada
    run_handler(Events.BOOKING_COMPLETED, data)
    assert entries(db, data["booking_id"]) == [("earning", 2_520_000, 3_000_000, 450_000, 30_000, b.DOCTOR)]


def test_unpaid_and_unconfirmed_bookings_are_ignored(db, run_handler) -> None:
    run_handler(Events.BOOKING_COMPLETED, b.completed(paid=0))  # at_clinic — pul platformadan o'tmagan
    run_handler(Events.BOOKING_CANCELLED, b.cancelled(previous="pending_payment", paid=0))
    assert db.scalar("SELECT count(*) FROM earning_entries") == 0


def test_cancellation_penalty_is_earning_minus_refund_share(db, run_handler) -> None:
    data = b.cancelled(refund=7_500_000)
    run_handler(Events.BOOKING_CANCELLED, data)
    run_handler(Events.REFUND_SUCCEEDED, b.refund(data["booking_id"], 7_500_000, 6_300_000))
    net = db.scalar("SELECT sum(amount_tiyin) FROM earning_entries WHERE booking_id = :b", b=data["booking_id"])
    assert net == 6_300_000  # jarimaning shifokor ulushi
    status = db.scalar("SELECT booking_status FROM earning_entries WHERE kind = 'earning'")
    assert status == "cancelled"


def test_refund_before_booking_event_is_linked_later(db, run_handler) -> None:
    data = b.no_show(by="doctor")
    rf = b.refund(data["booking_id"], 15_000_000, 12_600_000)
    run_handler(Events.REFUND_SUCCEEDED, rf)
    run_handler(Events.REFUND_SUCCEEDED, rf)
    assert entries(db, data["booking_id"]) == [("refund", -12_600_000, -15_000_000, -2_400_000, 0, None)]
    run_handler(Events.BOOKING_NO_SHOW, data)
    rows = entries(db, data["booking_id"])
    assert [r[0] for r in rows] == ["earning", "refund"] and {r[5] for r in rows} == {b.DOCTOR}
    assert sum(r[1] for r in rows) == 0  # shifokor kelmadi — to'liq qaytdi, shifokorga hech narsa


def test_reversal_deducts_and_orphan_refund_stays_unassigned(db, run_handler) -> None:
    data = b.completed()
    run_handler(Events.BOOKING_COMPLETED, data)
    run_handler(Events.PAYMENT_REVERSED, b.reversed_(data["booking_id"], 15_000_000, 12_600_000))
    assert sum(r[1] for r in entries(db, data["booking_id"])) == 0
    # confirmation_failed refund'i — bron hech qachon earning bermaydi: yetim yozuv payout'ga kirmaydi
    orphan = str(uuid.uuid4())
    run_handler(Events.REFUND_SUCCEEDED, b.refund(orphan, 15_000_000, 12_600_000))
    assert entries(db, orphan)[0][5] is None


def test_settings_changed_invalidates_cache(run_handler) -> None:
    from medbron_common.testing import sample_data

    run_handler(Events.ADMIN_SETTINGS_CHANGED, sample_data(Events.ADMIN_SETTINGS_CHANGED))
    assert run_handler.deps.settings.invalidated == 1
