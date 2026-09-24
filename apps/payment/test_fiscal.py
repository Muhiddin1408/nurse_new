"""B12 — fiskal chek elementlari."""

from decimal import Decimal
from unittest import mock

import pytest
from django.test import override_settings

from api.payments import fiscal
from apps.booking.models import BookingItem
from apps.payment.models import Payment

FISCAL = {"default_mxik_code": "10399002001000000", "default_package_code": "1500",
          "default_vat_percent": 0, "tin": "123456789"}


@pytest.mark.django_db
def test_zaklad_cheki_tolov_summasiga_teng(pending_booking, service):
    BookingItem.objects.create(booking=pending_booking, service=service, service_name="UZI",
                               price=Decimal("100000"), duration_minutes=10, mxik_code="X", package_code="1")
    payment = Payment.objects.filter(booking=pending_booking).first()
    payment.amount = Decimal("50000.01")  # g'alati summa — yaxlitlash qoldig'i
    rows = fiscal.receipt_items(payment)
    assert sum(r["price"] for r in rows) == payment.amount
    assert len(rows) == 2


@pytest.mark.django_db
@override_settings(FISCAL=FISCAL)
def test_bron_kodlarni_snapshot_qiladi_va_payme_detail(client_user, patient, doctor, clinic, service):
    from datetime import timedelta

    from django.utils import timezone

    from api.booking.services import BookingRequest, create_booking
    from api.payments.webhook import handle_payme_webhook
    from apps.schedule.models import TimeSlot

    service.mxik_code = "10399002001000000"
    service.save()
    start = (timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))
    booking = create_booking(BookingRequest(client_id=client_user.id, patient_id=patient.id, doctor_id=doctor.id,
                                            slot_id=slot.id, service_ids=[service.id]))
    item = booking.items.get()
    assert (item.mxik_code, item.package_code, item.vat_percent) == ("10399002001000000", "1500", 0)

    Payment.objects.create(booking=booking, provider="payme", amount=booking.prepay_amount, idempotency_key="f1")
    r = handle_payme_webhook(method="CheckPerformTransaction", params={
        "amount": int(booking.prepay_amount * 100), "account": {"booking_id": str(booking.id)}})
    detail = r["result"]["detail"]
    assert detail["items"][0]["code"] == "10399002001000000"
    assert sum(i["price"] for i in detail["items"]) == int(booking.prepay_amount * 100)


def test_qqs_narx_ichida():
    assert fiscal.vat_amount(Decimal("112000"), 12) == Decimal("12000.00")
    assert fiscal.vat_amount(Decimal("100"), 0) == 0


@pytest.mark.django_db
@override_settings(FISCAL=FISCAL, CLICK_SECRET_KEY="s", CLICK_SERVICE_ID="777", CLICK_MERCHANT_USER_ID="42")
def test_click_cheki_bir_marta_yuboriladi(pending_booking):
    from apps.payment.management.commands.submit_fiscal_receipts import submit_pending

    Payment.objects.filter(booking=pending_booking).update(
        provider="click", status="succeeded", provider_state={"click_paydoc_id": "5551"})
    resp = mock.Mock(status_code=200, json=lambda: {"error_code": 0})
    with mock.patch("requests.post", return_value=resp) as post:
        assert submit_pending() == (1, 0)
        assert submit_pending() == (0, 0)
    assert post.call_count == 1
    assert post.call_args.kwargs["json"]["received_ecash"] == int(pending_booking.prepay_amount * 100)
