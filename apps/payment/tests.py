"""Payme Merchant API kontrakt testlari (B2, B3) va checkout (B1)."""

import base64
from datetime import timedelta

import pytest
from django.utils import timezone

from api.payments.webhook import handle_payme_webhook
from apps.booking.models import Booking
from apps.payment.models import PaymeCallbackLog, Payment
from apps.schedule.models import TimeSlot

pytestmark = pytest.mark.payme_sandbox


def rpc(method, **params):
    return handle_payme_webhook(method=method, params=params)


def amount_of(booking):
    return int(booking.total_price * 100)


def account(booking):
    return {"booking_id": str(booking.id)}


def create_tx(booking, tx_id="payme-tx-001"):
    return rpc(
        "CreateTransaction",
        id=tx_id,
        time=int(timezone.now().timestamp() * 1000),
        amount=amount_of(booking),
        account=account(booking),
    )


# ---------------------------------------------------------------------------
# CheckPerformTransaction
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_check_perform_togri_sorovga_allow(pending_booking):
    result = rpc("CheckPerformTransaction", amount=amount_of(pending_booking), account=account(pending_booking))
    assert result["result"]["allow"] is True
    # B12: fiskal chek elementlari — summasi to'lov summasiga teng
    assert sum(i["price"] for i in result["result"]["detail"]["items"]) == amount_of(pending_booking)


@pytest.mark.django_db
def test_summa_mos_kelmasa_rad_etiladi(pending_booking):
    result = rpc(
        "CheckPerformTransaction",
        amount=amount_of(pending_booking) - 100_00,
        account=account(pending_booking),
    )
    assert result["error"]["code"] == -31001
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.PENDING_PAYMENT


@pytest.mark.django_db
def test_nomalum_booking_31050(pending_booking):
    import uuid

    result = rpc("CheckPerformTransaction", amount=100, account={"booking_id": str(uuid.uuid4())})
    assert result["error"]["code"] == -31050


@pytest.mark.django_db
def test_account_da_nomalum_maydon_rad_etiladi(pending_booking):
    result = rpc(
        "CheckPerformTransaction",
        amount=amount_of(pending_booking),
        account={"booking_id": str(pending_booking.id), "extra": "1"},
    )
    assert result["error"]["code"] == -31050


@pytest.mark.django_db
def test_muddati_otgan_bronga_payme_pul_yechmaydi(pending_booking):
    """B3 ning eng muhim qatori: hold o'tgan bo'lsa CheckPerform xato beradi."""
    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )
    result = rpc("CheckPerformTransaction", amount=amount_of(pending_booking), account=account(pending_booking))
    assert result["error"]["code"] == -31099


@pytest.mark.django_db
def test_bekor_qilingan_bronga_check_perform_xato(pending_booking):
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.CANCELLED)
    result = rpc("CheckPerformTransaction", amount=amount_of(pending_booking), account=account(pending_booking))
    assert result["error"]["code"] == -31099


# ---------------------------------------------------------------------------
# CreateTransaction
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_transaction_holati_1_va_hold_uzayadi(pending_booking):
    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        hold_expires_at=timezone.now() + timedelta(minutes=2)
    )
    result = create_tx(pending_booking)

    assert result["result"]["state"] == 1
    payment = Payment.objects.get(booking=pending_booking)
    assert payment.status == Payment.Status.PROCESSING
    assert payment.external_id == "payme-tx-001"

    slot = TimeSlot.objects.get(id=pending_booking.slot_id)
    assert slot.hold_expires_at > timezone.now() + timedelta(minutes=14)


@pytest.mark.django_db
def test_create_transaction_idempotent(pending_booking):
    first = create_tx(pending_booking)
    second = create_tx(pending_booking)
    assert first == second


@pytest.mark.django_db
def test_boshqa_id_bilan_ikkinchi_tranzaksiya_rad_etiladi(pending_booking):
    create_tx(pending_booking, "tx-a")
    result = create_tx(pending_booking, "tx-b")
    assert result["error"]["code"] == -31099


# ---------------------------------------------------------------------------
# PerformTransaction
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tolov_muvaffaqiyatli_bolsa_hammasi_tasdiqlanadi(pending_booking):
    create_tx(pending_booking)
    result = rpc("PerformTransaction", id="payme-tx-001")

    assert result["result"]["state"] == 2
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CONFIRMED
    assert TimeSlot.objects.get(id=pending_booking.slot_id).status == TimeSlot.Status.BOOKED


@pytest.mark.django_db
def test_perform_ikki_marta_kelsa_birinchi_natija_qaytadi(pending_booking):
    create_tx(pending_booking)
    first = rpc("PerformTransaction", id="payme-tx-001")
    second = rpc("PerformTransaction", id="payme-tx-001")

    assert first == second
    assert Payment.objects.get(booking=pending_booking).status == Payment.Status.SUCCEEDED


@pytest.mark.django_db
def test_perform_nomalum_tranzaksiya_31003(pending_booking):
    assert rpc("PerformTransaction", id="yoq")["error"]["code"] == -31003


@pytest.mark.django_db
def test_perform_bron_tasdiqlanmasa_ham_500_emas_refund_belgilanadi(pending_booking):
    """B3 fail-safe: poyga holatida bron bekor bo'lib qolgan, pul yechilgan."""
    create_tx(pending_booking)
    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.EXPIRED)

    result = rpc("PerformTransaction", id="payme-tx-001")

    assert result["result"]["state"] == 2
    payment = Payment.objects.get(booking=pending_booking)
    assert payment.status == Payment.Status.SUCCEEDED
    assert payment.needs_refund is True


@pytest.mark.django_db
def test_12_soatdan_eski_tranzaksiya_bajarilmaydi(pending_booking):
    create_tx(pending_booking)
    Payment.objects.filter(booking=pending_booking).update(
        provider_create_time=int((timezone.now() - timedelta(hours=13)).timestamp() * 1000)
    )
    result = rpc("PerformTransaction", id="payme-tx-001")
    assert result["error"]["code"] == -31008
    payment = Payment.objects.get(booking=pending_booking)
    assert payment.status == Payment.Status.CANCELLED
    assert payment.cancel_reason == 4


# ---------------------------------------------------------------------------
# CancelTransaction / CheckTransaction / GetStatement
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_yaratilgan_tranzaksiya_bekor_minus1_bron_kutishda_qoladi(pending_booking):
    create_tx(pending_booking)
    result = rpc("CancelTransaction", id="payme-tx-001", reason=3)

    assert result["result"]["state"] == -1
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.PENDING_PAYMENT


@pytest.mark.django_db
def test_bajarilgan_tolov_bekor_bolsa_kompensatsiya_ishlaydi(pending_booking):
    create_tx(pending_booking)
    rpc("PerformTransaction", id="payme-tx-001")
    result = rpc("CancelTransaction", id="payme-tx-001", reason=5)

    assert result["result"]["state"] == -2
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CANCELLED
    assert TimeSlot.objects.get(id=pending_booking.slot_id).status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_bekor_qilish_idempotent(pending_booking):
    create_tx(pending_booking)
    rpc("PerformTransaction", id="payme-tx-001")
    first = rpc("CancelTransaction", id="payme-tx-001", reason=5)
    second = rpc("CancelTransaction", id="payme-tx-001", reason=5)
    assert first == second


@pytest.mark.django_db
def test_check_transaction_holatni_qaytaradi(pending_booking):
    create_tx(pending_booking)
    rpc("PerformTransaction", id="payme-tx-001")
    result = rpc("CheckTransaction", id="payme-tx-001")["result"]
    assert result["state"] == 2
    assert result["perform_time"] > 0
    assert result["cancel_time"] == 0


@pytest.mark.django_db
def test_get_statement_davrdagi_tranzaksiyalar(pending_booking):
    create_tx(pending_booking)
    now = int(timezone.now().timestamp() * 1000)
    txs = rpc("GetStatement", **{"from": now - 60_000, "to": now + 60_000})["result"]["transactions"]
    assert [t["id"] for t in txs] == ["payme-tx-001"]
    assert txs[0]["account"] == account(pending_booking)


@pytest.mark.django_db
def test_nomalum_metod_32601(pending_booking):
    assert rpc("Foo")["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# HTTP qatlami: auth, jurnal, checkout
# ---------------------------------------------------------------------------


@pytest.fixture
def payme_key(settings):
    settings.PAYME_SECRET_KEY = "test-key"
    settings.PAYME_MERCHANT_ID = "merchant-1"
    return "test-key"


def _auth(key):
    return "Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode()


@pytest.mark.django_db
def test_webhook_notogri_kalit_32504_va_jurnalga_yoziladi(pending_booking, payme_key):
    from rest_framework.test import APIClient

    resp = APIClient().post(
        "/api/v1/payment/payme/webhook",
        {"id": 1, "method": "CheckTransaction", "params": {"id": "x"}},
        format="json",
        HTTP_AUTHORIZATION=_auth("wrong"),
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32504
    assert PaymeCallbackLog.objects.count() == 1


@pytest.mark.django_db
def test_webhook_ip_allowlist(pending_booking, payme_key, settings):
    from rest_framework.test import APIClient

    settings.PAYME_ALLOWED_IPS = ["185.234.113.1"]
    resp = APIClient().post(
        "/api/v1/payment/payme/webhook",
        {"id": 1, "method": "CheckTransaction", "params": {"id": "x"}},
        format="json",
        HTTP_AUTHORIZATION=_auth(payme_key),
        REMOTE_ADDR="10.0.0.1",
    )
    assert resp.json()["error"]["code"] == -32504


@pytest.mark.django_db
def test_webhook_togri_kalit_bilan_ishlaydi(pending_booking, payme_key):
    from rest_framework.test import APIClient

    resp = APIClient().post(
        "/api/v1/payment/payme/webhook",
        {
            "id": 7,
            "method": "CheckPerformTransaction",
            "params": {"amount": amount_of(pending_booking), "account": account(pending_booking)},
        },
        format="json",
        HTTP_AUTHORIZATION=_auth(payme_key),
    )
    body = resp.json()
    assert body["id"] == 7
    assert body["result"]["allow"] is True and body["result"]["detail"]["items"]


@pytest.mark.django_db
def test_checkout_idempotent_va_begona_bron_404(pending_booking, payme_key):
    from rest_framework.test import APIClient

    from apps.account.models import User

    api = APIClient()
    api.force_authenticate(pending_booking.client)
    url = f"/api/v1/payment/bookings/{pending_booking.id}/checkout"

    first = api.post(url, {"provider": "payme"}, format="json")
    second = api.post(url, {"provider": "payme"}, format="json")
    assert first.status_code == 200
    assert first.json()["payment_id"] == second.json()["payment_id"]
    assert first.json()["checkout_url"].startswith("https://checkout.paycom.uz/")
    assert first.json()["amount"] == int(pending_booking.total_price)

    stranger = User.objects.create_user(phone="+998901119999")
    api.force_authenticate(stranger)
    assert api.post(url, {"provider": "payme"}, format="json").status_code == 404


@pytest.mark.django_db
def test_checkout_tasdiqlangan_bronga_409(pending_booking, payme_key):
    from rest_framework.test import APIClient

    Booking.objects.filter(id=pending_booking.id).update(status=Booking.Status.CONFIRMED)
    api = APIClient()
    api.force_authenticate(pending_booking.client)
    resp = api.post(f"/api/v1/payment/bookings/{pending_booking.id}/checkout", {"provider": "payme"}, format="json")
    assert resp.status_code == 409
