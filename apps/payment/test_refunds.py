"""Sprint 1.2 — bekor qilish + refund oqimi (B4), provayder abstraksiyasi (B5)."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking.services import BookingError, cancel_booking, mark_no_show
from api.payments import providers, refunds
from apps.booking.models import Booking
from apps.payment.models import Payment, ProviderRequestLog, Refund
from apps.schedule.models import TimeSlot
from apps.utils.models import OutboxEvent


def paid(booking, *, hours_until=30):
    """Bron to'langan va tasdiqlangan, qabulga `hours_until` soat bor."""
    start = timezone.now() + timedelta(hours=hours_until)
    TimeSlot.objects.filter(id=booking.slot_id).update(
        status=TimeSlot.Status.BOOKED, hold_expires_at=None,
        start_at=start, end_at=start + timedelta(minutes=30),
    )
    Booking.objects.filter(id=booking.id).update(status=Booking.Status.CONFIRMED)
    Payment.objects.filter(booking=booking).update(status=Payment.Status.SUCCEEDED, external_id="tx-9")
    booking.refresh_from_db()
    return booking


class OkProvider:
    name = "payme"

    def refund(self, payment, amount, reason):
        return providers.RefundDTO(external_refund_id="rf-1", status="succeeded")


class FlakyProvider:
    name = "payme"

    def refund(self, payment, amount, reason):
        raise providers.ProviderError("timeout")


@pytest.fixture
def ok_provider():
    original = providers.get_provider("payme")
    providers.register_provider("payme", OkProvider())
    yield
    providers.register_provider("payme", original)


# --- cancel_booking + siyosat -----------------------------------------------


@pytest.mark.django_db
def test_24_soatdan_oldin_bekor_100_foiz_refund_navbatga(pending_booking):
    b = paid(pending_booking, hours_until=30)
    cancel_booking(b.id, actor="client")

    b.refresh_from_db()
    assert b.status == Booking.Status.CANCELLED
    refund = Refund.objects.get(booking=b)
    assert refund.amount == b.total_price
    assert refund.reason == "full_refund"
    assert refund.status == Refund.Status.PENDING
    assert OutboxEvent.objects.filter(event_type="RefundRequested").count() == 1


@pytest.mark.django_db
def test_2_24_soat_oraligida_50_foiz(pending_booking):
    b = paid(pending_booking, hours_until=5)
    cancel_booking(b.id, actor="client")
    assert Refund.objects.get(booking=b).amount == b.total_price / 2


@pytest.mark.django_db
def test_kech_bekor_refund_yaratilmaydi(pending_booking):
    b = paid(pending_booking, hours_until=1)
    cancel_booking(b.id, actor="client")
    assert not Refund.objects.exists()


@pytest.mark.django_db
def test_boshlangan_qabulni_mijoz_bekor_qila_olmaydi(pending_booking):
    b = paid(pending_booking, hours_until=-0.1)
    with pytest.raises(BookingError, match="boshlangan"):
        cancel_booking(b.id, actor="client")
    b.refresh_from_db()
    assert b.status == Booking.Status.CONFIRMED


@pytest.mark.django_db
def test_tolanmagan_bron_refundsiz_bekor(pending_booking):
    cancel_booking(pending_booking.id, actor="client")
    assert not Refund.objects.exists()


@pytest.mark.django_db
def test_shifokor_kelmasa_toliq_refund(pending_booking):
    b = paid(pending_booking, hours_until=-1)
    mark_no_show(b.id, absent="doctor", reported_by="admin")
    refund = Refund.objects.get(booking=b)
    assert (refund.amount, refund.reason) == (b.total_price, "doctor_no_show")


@pytest.mark.django_db
def test_mijoz_kelmasa_refund_yoq(pending_booking):
    b = paid(pending_booking, hours_until=-1)
    mark_no_show(b.id, absent="client", reported_by="doctor")
    assert not Refund.objects.exists()


# --- request_refund invariantlari --------------------------------------------


@pytest.mark.django_db
def test_refund_idempotent_va_summadan_oshmaydi(pending_booking):
    b = paid(pending_booking)
    r1 = refunds.request_refund(booking_id=b.id, amount=Decimal("100000"), reason="a", initiated_by="admin")
    r2 = refunds.request_refund(booking_id=b.id, amount=Decimal("100000"), reason="a", initiated_by="admin")
    assert r1.id == r2.id
    r3 = refunds.request_refund(booking_id=b.id, amount=Decimal("999999"), reason="b", initiated_by="admin")
    assert r3.amount == b.total_price - Decimal("100000")
    assert refunds.request_refund(booking_id=b.id, amount=Decimal("1"), reason="c", initiated_by="admin") is None


# --- worker ------------------------------------------------------------------


@pytest.mark.django_db
def test_worker_muvaffaqiyat_payment_refunded_va_sms_hodisasi(pending_booking, ok_provider):
    b = paid(pending_booking, hours_until=30)
    cancel_booking(b.id, actor="client")
    call_command("run_refunds")

    refund = Refund.objects.get(booking=b)
    assert refund.status == Refund.Status.SUCCEEDED
    assert refund.external_refund_id == "rf-1"
    assert Payment.objects.get(booking=b).status == Payment.Status.REFUNDED
    assert OutboxEvent.objects.filter(event_type="RefundSucceeded").exists()


@pytest.mark.django_db
def test_qisman_refund_partially_refunded(pending_booking, ok_provider):
    b = paid(pending_booking, hours_until=5)
    cancel_booking(b.id, actor="client")
    call_command("run_refunds")
    assert Payment.objects.get(booking=b).status == Payment.Status.PARTIALLY_REFUNDED


@pytest.mark.django_db
def test_vaqtinchalik_xato_backoff_5_dan_keyin_failed(pending_booking):
    b = paid(pending_booking, hours_until=30)
    cancel_booking(b.id, actor="client")
    original = providers.get_provider("payme")
    providers.register_provider("payme", FlakyProvider())
    try:
        for i in range(5):
            Refund.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
            refunds.process_due_refunds()
            refund = Refund.objects.get()
            if i < 4:
                assert refund.status == Refund.Status.PENDING
                assert refund.next_attempt_at > timezone.now()
    finally:
        providers.register_provider("payme", original)
    refund = Refund.objects.get()
    assert refund.status == Refund.Status.FAILED
    assert refund.attempts == 5


@pytest.mark.django_db
def test_payme_merchant_refund_qolda_navbatga(pending_booking):
    b = paid(pending_booking, hours_until=30)
    cancel_booking(b.id, actor="client")
    refunds.process_due_refunds()
    assert Refund.objects.get().status == Refund.Status.MANUAL_REQUIRED


@pytest.mark.django_db
def test_payme_kabinetda_bekor_qilsa_qolda_refund_yopiladi(pending_booking):
    from api.payments.webhook import handle_payme_webhook

    b = paid(pending_booking, hours_until=30)
    cancel_booking(b.id, actor="client")
    refunds.process_due_refunds()  # -> manual_required

    result = handle_payme_webhook(method="CancelTransaction", params={"id": "tx-9", "reason": 5})
    assert result["result"]["state"] == -2
    assert Refund.objects.get().status == Refund.Status.SUCCEEDED
    assert Payment.objects.get(booking=b).status == Payment.Status.REFUNDED


# --- B3 fail-safe -> avtomatik refund ---------------------------------------


@pytest.mark.django_db
def test_failsafe_refund_yaratadi(pending_booking):
    from api.payments.webhook import handle_payme_webhook

    handle_payme_webhook(method="CreateTransaction", params={
        "id": "tx-f", "time": 1, "amount": int(pending_booking.total_price * 100),
        "account": {"booking_id": str(pending_booking.id)},
    })
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.EXPIRED)
    handle_payme_webhook(method="PerformTransaction", params={"id": "tx-f"})

    refund = Refund.objects.get()
    assert refund.reason == "booking_unavailable"
    assert refund.amount == pending_booking.total_price


# --- SMS ---------------------------------------------------------------------


@pytest.mark.django_db  # C13: kanal tanlash mijoz sozlamalarini o'qiydi
def test_refund_sms_handlerlari():
    from api.notifications import consumer

    data = {"client_phone": "+998901112233", "booking_number": "MB-1", "amount": 75000, "reason": "booking_unavailable"}
    with mock.patch("api.notifications.services.send_sms") as send:
        consumer._handle_refund_requested(data)
        consumer._handle_refund_requested({**data, "reason": "full_refund"})
        consumer._handle_refund_succeeded(data)
    templates = [c.kwargs["template"] for c in send.call_args_list]
    assert templates == ["payment_booking_unavailable", "refund_succeeded"]
    assert send.call_args_list[1].kwargs["params"]["amount"] == "75 000"


# --- preview endpoint --------------------------------------------------------


@pytest.mark.django_db
def test_preview_endpoint_va_bekor_qilish_bir_xil_summa(pending_booking):
    b = paid(pending_booking, hours_until=5)
    api = APIClient()
    api.force_authenticate(b.client)

    preview = api.get(f"/api/v1/booking/bookings/{b.id}/cancellation-preview").json()
    assert preview["allowed"] is True
    assert Decimal(preview["refund_amount"]) == b.total_price / 2

    assert api.post(f"/api/v1/booking/bookings/{b.id}/cancel", {}, format="json").status_code == 200
    assert Refund.objects.get().amount == Decimal(preview["refund_amount"])


@pytest.mark.django_db
def test_boshlangan_qabul_cancel_endpoint_409(pending_booking):
    b = paid(pending_booking, hours_until=-0.1)
    api = APIClient()
    api.force_authenticate(b.client)
    assert api.post(f"/api/v1/booking/bookings/{b.id}/cancel", {}, format="json").status_code == 409


# --- B5 provayder chaqiruvi ---------------------------------------------------


@pytest.mark.django_db
def test_provider_call_retry_log_va_circuit_breaker():
    providers._breakers.clear()
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise providers.ProviderError("503")
        return {"ok": True}

    with mock.patch("api.payments.providers.time.sleep"):
        assert providers.call("test", "status", {"a": 1}, flaky, idempotent=True) == {"ok": True}
    assert calls["n"] == 3
    assert ProviderRequestLog.objects.filter(provider="test").count() == 3

    # idempotent bo'lmagan operatsiya qayta urinilmaydi
    calls["n"] = 0
    with pytest.raises(providers.ProviderError):
        providers.call("test2", "refund", {}, flaky, idempotent=False)
    assert calls["n"] == 1

    # 5 ta ketma-ket xato -> breaker ochiladi
    def always_fail():
        raise providers.ProviderError("down")

    with mock.patch("api.payments.providers.time.sleep"):
        for _ in range(5):
            with pytest.raises(providers.ProviderError):
                providers.call("down", "x", {}, always_fail, idempotent=False)
    with pytest.raises(providers.ProviderUnavailable):
        providers.call("down", "x", {}, always_fail, idempotent=False)
