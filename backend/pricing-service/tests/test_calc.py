"""Sof narx hisobi — monolit testlaridagi holatlar (apps/booking/test_discounts.py, test_payment_mode.py,
test_packages_and_time_pricing.py) tiyinda."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.services import calc

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
DOC = uuid.uuid4()
SOM = 100  # 1 so'm = 100 tiyin


def promo(**kw) -> calc.PromoTerms:
    base = dict(
        id=uuid.uuid4(), code="BAHOR", kind="percent", value=20, max_discount_tiyin=None, min_amount_tiyin=0,
        valid_from=None, valid_until=None, max_uses=None, max_uses_per_user=1, first_booking_only=False,
        doctor_id=None, borne_by="platform",
    )  # fmt: skip
    return calc.PromoTerms(**{**base, **kw})


def apply(terms, original=150_000 * SOM, usage=(0, 0), previous=False):
    return calc.promo_discount(
        terms, original_tiyin=original, doctor_id=DOC, has_previous_booking=previous,
        usage=calc.PromoUsage(*usage), now=NOW,
    )  # fmt: skip


# --- vaqtga qarab narx (C12) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("percent", "expected"),
    [(-15, 85_000), (20, 120_000), (-900, 10_000), (500, 200_000), (0, 100_000)],
)
def test_time_adjust_clamped(percent: int, expected: int) -> None:
    sid = uuid.uuid4()
    assert calc.adjusted_prices({sid: 100_000 * SOM}, percent) == {sid: expected * SOM}


def test_zero_percent_keeps_odd_price() -> None:
    sid = uuid.uuid4()
    assert calc.adjusted_prices({sid: 15_000_033}, 0) == {sid: 15_000_033}


# --- promo -----------------------------------------------------------------------------------


def test_promo_percent_fixed_caps() -> None:
    assert apply(promo()).amount_tiyin == 30_000 * SOM  # 20% of 150 000
    assert apply(promo(kind="fixed", value=50_000 * SOM)).amount_tiyin == 50_000 * SOM
    assert apply(promo(max_discount_tiyin=10_000 * SOM)).amount_tiyin == 10_000 * SOM
    assert apply(promo(kind="fixed", value=999_999 * SOM)).amount_tiyin == 150_000 * SOM  # ≤ total
    d = apply(promo(borne_by="doctor"))
    assert d.kind == "promo" and d.borne_by == "doctor" and d.promo_code == "BAHOR"


@pytest.mark.parametrize(
    ("terms", "kwargs", "reason"),
    [
        (None, {}, "not_found"),
        (promo(valid_until=NOW - timedelta(seconds=1)), {}, "expired"),
        (promo(valid_from=NOW + timedelta(days=1)), {}, "expired"),
        (promo(doctor_id=uuid.uuid4()), {}, "not_for_doctor"),
        (promo(min_amount_tiyin=200_000 * SOM), {}, "min_amount"),
        (promo(max_uses=1), {"usage": (1, 0)}, "limit_reached"),
        (promo(max_uses_per_user=1), {"usage": (5, 1)}, "already_used"),
        (promo(first_booking_only=True), {"previous": True}, "first_booking_only"),
    ],
)
def test_promo_validation_order(terms, kwargs, reason) -> None:
    with pytest.raises(calc.PromoInvalid) as err:
        apply(terms, **kwargs)
    assert err.value.reason == reason and err.value.message


def test_promo_for_same_doctor_ok() -> None:
    assert apply(promo(doctor_id=DOC)).amount_tiyin > 0


# --- eng yaxshi chegirma (qo'shilmaydi) ------------------------------------------------------


def inputs(**kw) -> calc.DiscountInputs:
    sid = kw.pop("sid", uuid.uuid4())
    original = kw.pop("original", 150_000 * SOM)
    return calc.DiscountInputs(original_tiyin=original, prices=kw.pop("prices", {sid: original}), **kw)


def test_first_booking_only_without_previous() -> None:
    assert calc.best_discount(inputs(first_booking_percent=10, has_previous_booking=False)).amount_tiyin == 15_000 * SOM
    assert calc.best_discount(inputs(first_booking_percent=10, has_previous_booking=True)) == calc.NO_DISCOUNT
    d = calc.best_discount(inputs(first_booking_percent=10, has_previous_booking=False))
    assert d.kind == "first_booking" and d.borne_by == "platform"


def test_follow_up_wins_over_smaller_and_free_visit() -> None:
    sid = uuid.uuid4()
    offers = [calc.EnrollmentOffer(uuid.uuid4(), sid, 10, 3)]
    promo10 = apply(promo(value=10))
    d = calc.best_discount(
        inputs(
            sid=sid, follow_up_percent=30, has_recent_completed_with_doctor=True, package_offers=offers, promo=promo10
        )
    )
    assert (d.kind, d.amount_tiyin, d.borne_by) == ("follow_up", 45_000 * SOM, "doctor")
    free = calc.best_discount(inputs(follow_up_percent=100, has_recent_completed_with_doctor=True))
    assert free.amount_tiyin == 150_000 * SOM
    assert calc.best_discount(inputs(follow_up_percent=50)) == calc.NO_DISCOUNT  # tashrif yo'q


def test_package_discount_applies_to_that_service_only() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    prices = {a: 120_000 * SOM, b: 30_000 * SOM}  # 150 000 → paket faqat A ga
    offers = [calc.EnrollmentOffer(uuid.uuid4(), b, 50, 0), calc.EnrollmentOffer(uuid.uuid4(), a, 10, 2)]
    d = calc.best_discount(inputs(original=150_000 * SOM, prices=prices, package_offers=offers))
    assert (d.kind, d.amount_tiyin, d.borne_by) == ("package", 12_000 * SOM, "doctor")
    assert d.enrollment_id == offers[1].enrollment_id  # seans qolmagan birinchisi o'tkazib yuborildi


# --- komissiya (B8) --------------------------------------------------------------------------


def test_split_amount_example() -> None:
    s = calc.split_amount(150_000 * SOM, 1500, 100)
    assert (s.platform_fee_tiyin, s.provider_fee_tiyin, s.doctor_payout_tiyin) == (
        22_500 * SOM,
        1_500 * SOM,
        126_000 * SOM,
    )


@pytest.mark.parametrize("total", [1, 3, 99, 101, 12_345_67, 99_999_999])
def test_split_invariant_rounding_to_doctor(total: int) -> None:
    s = calc.split_amount(total, 1500, 100)
    assert s.platform_fee_tiyin + s.provider_fee_tiyin + s.doctor_payout_tiyin == total


def test_platform_borne_discount_keeps_doctor_payout() -> None:
    d = calc.Discount("promo", 20_000 * SOM, "platform")
    s = calc.split_with_discount(100_000 * SOM, d, 1500, 100)
    full = calc.split_amount(100_000 * SOM, 1500, 100)
    assert s.total_tiyin == 80_000 * SOM and s.doctor_payout_tiyin == full.doctor_payout_tiyin
    # 80 000 − 800 (provayder) − 84 000 (shifokor) = −4 800: platforma marketing xarajati
    assert (s.provider_fee_tiyin, s.platform_fee_tiyin) == (800 * SOM, -4_800 * SOM)
    assert s.platform_fee_tiyin + s.provider_fee_tiyin + s.doctor_payout_tiyin == s.total_tiyin


def test_doctor_borne_discount_normal_split() -> None:
    s = calc.split_with_discount(100_000 * SOM, calc.Discount("follow_up", 50_000 * SOM, "doctor"), 1500, 100)
    assert s.platform_fee_tiyin == 7_500 * SOM and s.total_tiyin == 50_000 * SOM


def test_platform_fee_can_be_negative() -> None:
    s = calc.split_with_discount(100_000 * SOM, calc.Discount("promo", 50_000 * SOM, "platform"), 1500, 100)
    assert s.platform_fee_tiyin < 0
    assert s.platform_fee_tiyin + s.provider_fee_tiyin + s.doctor_payout_tiyin == 50_000 * SOM


# --- to'lov rejimi (B10) ---------------------------------------------------------------------


def mode(**kw) -> calc.PaymentPlan:
    base = dict(total_tiyin=100_000 * SOM, is_home_visit=False, doctor_mode="prepaid", client_no_shows=0,
                deposit_percent=20, force_prepaid_after_no_shows=2)  # fmt: skip
    return calc.resolve_payment_mode(**{**base, **kw})


def test_payment_modes() -> None:
    assert mode(total_tiyin=0) == calc.PaymentPlan("at_clinic", 0)
    assert mode(is_home_visit=True, doctor_mode="at_clinic") == calc.PaymentPlan("prepaid", 100_000 * SOM)
    assert mode(doctor_mode="at_clinic", client_no_shows=2) == calc.PaymentPlan("prepaid", 100_000 * SOM)
    assert mode(doctor_mode="at_clinic", client_no_shows=1) == calc.PaymentPlan("at_clinic", 0)
    assert mode(doctor_mode="deposit") == calc.PaymentPlan("deposit", 20_000 * SOM)
    assert mode(doctor_mode="deposit", total_tiyin=1) == calc.PaymentPlan("at_clinic", 0)  # depozit 0 ga yaxlitlandi
    assert mode(doctor_mode="deposit", total_tiyin=3) == calc.PaymentPlan("deposit", 1)  # 0.6 → 1 (half-up)
    assert mode() == calc.PaymentPlan("prepaid", 100_000 * SOM)
    assert mode(doctor_mode="at_clinic", client_no_shows=5, force_prepaid_after_no_shows=0) == calc.PaymentPlan(
        "at_clinic", 0
    )
