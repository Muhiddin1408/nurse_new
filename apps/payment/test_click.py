"""B6 — Click SHOP API: Prepare/Complete, imzo, idempotentlik, bekor qilish, refund."""

from decimal import Decimal
from unittest import mock

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from api.payments import click
from apps.booking.models import Booking
from apps.payment.models import LedgerTransaction, Payment
from apps.schedule.models import TimeSlot

CLICK = dict(CLICK_SECRET_KEY="click-secret", CLICK_SERVICE_ID="777", CLICK_ALLOWED_IPS=[])


def _signed(params: dict, *, complete: bool) -> dict:
    with override_settings(**CLICK):
        return {**params, "sign_string": click.expected_sign(params, with_prepare_id=complete)}


@pytest.fixture
def click_payment(pending_booking):
    Payment.objects.filter(booking=pending_booking).delete()
    return Payment.objects.create(booking=pending_booking, provider=Payment.Provider.CLICK,
                                  amount=pending_booking.prepay_amount, idempotency_key="click-1")


def _prepare_params(booking, amount, trans_id="9001"):
    return {"click_trans_id": trans_id, "service_id": "777", "click_paydoc_id": "5551",
            "merchant_trans_id": str(booking.id), "amount": f"{amount:.2f}", "action": "0",
            "error": "0", "error_note": "Success", "sign_time": "2026-09-18 10:00:00"}


def _post(path, data):
    with override_settings(**CLICK):
        return APIClient().post(f"/api/v1/payment/click/{path}", data, format="multipart").json()


@pytest.mark.django_db
def test_toliq_oqim_prepare_complete(click_payment, pending_booking):
    p = _prepare_params(pending_booking, click_payment.amount)
    prep = _post("prepare", _signed(p, complete=False))
    assert prep["error"] == 0 and prep["merchant_prepare_id"]
    # Takroriy Prepare — o'sha prepare_id
    assert _post("prepare", _signed(p, complete=False))["merchant_prepare_id"] == prep["merchant_prepare_id"]

    c = {**p, "action": "1", "merchant_prepare_id": str(prep["merchant_prepare_id"])}
    done = _post("complete", _signed(c, complete=True))
    assert done["error"] == 0 and done["merchant_confirm_id"]
    assert _post("complete", _signed(c, complete=True))["merchant_confirm_id"] == done["merchant_confirm_id"]

    click_payment.refresh_from_db()
    assert click_payment.status == Payment.Status.SUCCEEDED
    assert click_payment.provider_state["click_paydoc_id"] == "5551"
    assert Booking.objects.get(id=pending_booking.id).status == Booking.Status.CONFIRMED
    assert LedgerTransaction.objects.filter(kind="payment_succeeded").count() == 1  # bir marta


@pytest.mark.django_db
def test_notogri_imzo_va_summa(click_payment, pending_booking):
    p = _prepare_params(pending_booking, click_payment.amount)
    assert _post("prepare", {**p, "sign_string": "bad"})["error"] == click.ERR_SIGN
    wrong = _prepare_params(pending_booking, click_payment.amount + 1)
    assert _post("prepare", _signed(wrong, complete=False))["error"] == click.ERR_AMOUNT
    assert Payment.objects.get(id=click_payment.id).status == Payment.Status.CREATED


@pytest.mark.django_db
def test_kalit_bolmasa_fail_closed(click_payment, pending_booking):
    p = _signed(_prepare_params(pending_booking, click_payment.amount), complete=False)
    with override_settings(**{**CLICK, "CLICK_SECRET_KEY": ""}):
        assert APIClient().post("/api/v1/payment/click/prepare", p).json()["error"] == click.ERR_SIGN


@pytest.mark.django_db
def test_muddati_otgan_bronga_pul_yechilmaydi(click_payment, pending_booking):
    TimeSlot.objects.filter(id=pending_booking.slot_id).update(status=TimeSlot.Status.FREE, hold_expires_at=None)
    p = _prepare_params(pending_booking, click_payment.amount)
    assert _post("prepare", _signed(p, complete=False))["error"] == click.ERR_ORDER_NOT_FOUND


@pytest.mark.django_db
def test_click_bekor_qilsa_tolov_cancelled(click_payment, pending_booking):
    p = _prepare_params(pending_booking, click_payment.amount)
    prep = _post("prepare", _signed(p, complete=False))
    c = {**p, "action": "1", "merchant_prepare_id": str(prep["merchant_prepare_id"]), "error": "-5017"}
    assert _post("complete", _signed(c, complete=True))["error"] == click.ERR_CANCELLED
    assert Payment.objects.get(id=click_payment.id).status == Payment.Status.CANCELLED
    assert Booking.objects.get(id=pending_booking.id).status == Booking.Status.PENDING_PAYMENT


@pytest.mark.django_db
def test_click_reversal_toliq_summa(click_payment):
    from api.payments.providers import ClickProvider, ManualRefundRequired

    click_payment.provider_state = {"click_paydoc_id": "5551"}
    resp = mock.Mock(status_code=200, json=lambda: {"error_code": 0, "payment_id": 5551})
    with override_settings(**CLICK, CLICK_MERCHANT_USER_ID="42"), mock.patch("requests.delete", return_value=resp) as d:
        result = ClickProvider().refund(click_payment, click_payment.amount, "x")
        assert result.status == "succeeded"
        assert d.call_args.kwargs["headers"]["Auth"].startswith("42:")
        with pytest.raises(ManualRefundRequired):
            ClickProvider().refund(click_payment, click_payment.amount - Decimal("1"), "x")
