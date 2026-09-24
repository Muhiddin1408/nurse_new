"""C2 — `evaluate_cancellation` sof funksiyasi, barcha tarmoqlar (100% qamrov)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from api.booking.cancellation import CancellationPolicy, evaluate_cancellation, policy_from_settings

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
PAID = Decimal("150000.00")


def ev(hours_left, *, status="confirmed", actor="client", paid=PAID, policy=CancellationPolicy()):
    return evaluate_cancellation(
        status=status, starts_at=NOW + timedelta(hours=hours_left),
        paid_amount=paid, actor=actor, now=NOW, policy=policy,
    )


def test_tolanmagan_bron_bepul_bekor():
    o = ev(1, status="pending_payment")
    assert (o.allowed, o.refund_amount, o.penalty_amount, o.reason_code) == (True, 0, 0, "unpaid")


@pytest.mark.parametrize("status", ["cancelled", "expired", "completed", "no_show"])
def test_yakuniy_holat_bekor_qilinmaydi(status):
    o = ev(48, status=status)
    assert not o.allowed and o.reason_code == "final_state"


def test_boshlangan_qabul_bekor_qilinmaydi():
    o = ev(0)
    assert not o.allowed and o.reason_code == "already_started"
    assert not ev(-1).allowed


@pytest.mark.parametrize("actor", ["doctor", "admin", "system"])
def test_shifokor_admin_tizim_har_doim_100(actor):
    o = ev(0.5, actor=actor)
    assert o.allowed and o.refund_amount == PAID and o.penalty_amount == 0
    assert o.reason_code == f"{actor}_cancelled"


def test_24_soatdan_kop_100_foiz():
    o = ev(25)
    assert (o.refund_amount, o.penalty_amount, o.reason_code) == (PAID, 0, "full_refund")


def test_chegara_aniq_24_soat_qisman():
    assert ev(24).reason_code == "partial_refund"


def test_2_24_soat_50_foiz():
    o = ev(10)
    assert o.refund_amount == Decimal("75000.00")
    assert o.penalty_amount == Decimal("75000.00")
    assert o.reason_code == "partial_refund"


def test_2_soatdan_kam_refund_yoq():
    o = ev(1)
    assert (o.refund_amount, o.penalty_amount, o.reason_code) == (0, PAID, "late_cancellation")


def test_toq_summa_yarimi_yuvarlanadi_va_yigindi_saqlanadi():
    o = ev(10, paid=Decimal("100001"))
    assert o.refund_amount + o.penalty_amount == Decimal("100001.00")


def test_sozlama_bilan_siyosat(settings):
    settings.CANCELLATION_POLICY = {"full_refund_hours": 48, "partial_refund_hours": 6, "partial_refund_percent": 30}
    policy = policy_from_settings()
    assert ev(30, policy=policy).refund_amount == Decimal("45000.00")
    assert ev(49, policy=policy).reason_code == "full_refund"
    assert ev(5, policy=policy).reason_code == "late_cancellation"
