"""Sprint 1.3 — komissiya snapshot (B8), ledger (B8), reconciliation (B7), nizolar (B11)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking.pricing import split_amount
from api.payments import ledger, reconciliation, refunds
from api.payments.providers import TransactionDTO
from api.payments.webhook import handle_payme_webhook
from apps.booking.models import Booking, Dispute
from apps.payment.models import LedgerEntry, Payment, PaymentDiscrepancy, Refund

A = LedgerEntry.Account


def pay(booking, tx="tx-m"):
    handle_payme_webhook(method="CreateTransaction", params={
        "id": tx, "time": 1, "amount": int(booking.total_price * 100),
        "account": {"booking_id": str(booking.id)},
    })
    handle_payme_webhook(method="PerformTransaction", params={"id": tx})
    booking.refresh_from_db()
    return Payment.objects.get(external_id=tx)


@pytest.fixture
def split_booking(pending_booking):
    s = split_amount(pending_booking.total_price, Decimal("0.15"), Decimal("0.01"))
    Booking.objects.filter(id=pending_booking.id).update(
        commission_rate=s.commission_rate, provider_fee_rate=s.provider_fee_rate,
        platform_fee=s.platform_fee, provider_fee=s.provider_fee, doctor_payout=s.doctor_payout,
    )
    pending_booking.refresh_from_db()
    return pending_booking


# --- B8 pricing -------------------------------------------------------------


def test_hujjatdagi_misol_150000():
    s = split_amount(Decimal("150000"), Decimal("0.15"), Decimal("0.01"))
    assert (s.platform_fee, s.provider_fee, s.doctor_payout) == (Decimal("22500.00"), Decimal("1500.00"), Decimal("126000.00"))


def test_yuvarlashda_tiyin_yoqolmaydi():
    s = split_amount(Decimal("99999.99"), Decimal("0.1537"), Decimal("0.0123"))
    assert s.platform_fee + s.provider_fee + s.doctor_payout == Decimal("99999.99")


@pytest.mark.django_db
def test_bron_yaratilganda_stavka_muzlatiladi(client_user, patient, doctor, clinic, service, settings):
    from api.booking.services import BookingRequest, create_booking
    from apps.schedule.models import TimeSlot

    settings.PLATFORM_COMMISSION_RATE = "0.20"
    start = (timezone.now() + timedelta(hours=3)).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))
    b = create_booking(BookingRequest(client_id=client_user.id, patient_id=patient.id, doctor_id=doctor.id,
                                      slot_id=slot.id, service_ids=[service.id]))
    assert b.commission_rate == Decimal("0.2000")
    assert b.platform_fee == Decimal("30000.00")

    settings.PLATFORM_COMMISSION_RATE = "0.50"
    b.refresh_from_db()
    assert b.platform_fee == Decimal("30000.00")  # o'zgarmadi


# --- B8 ledger --------------------------------------------------------------


@pytest.mark.django_db
def test_tolov_ledgerga_balansli_tushadi(split_booking):
    pay(split_booking)
    assert ledger.trial_balance() == 0
    assert ledger.balance(A.CLIENT_PAYMENTS) == Decimal("150000.00")
    assert ledger.balance(A.DOCTOR_PAYABLE, doctor_id=split_booking.doctor_id) == Decimal("126000.00")
    assert ledger.balance(A.PLATFORM_REVENUE) == Decimal("22500.00")
    assert ledger.balance(A.PROVIDER_FEES) == Decimal("1500.00")


@pytest.mark.django_db
def test_perform_takrorlansa_ledger_ikki_marta_yozilmaydi(split_booking):
    pay(split_booking)
    handle_payme_webhook(method="PerformTransaction", params={"id": "tx-m"})
    assert LedgerEntry.objects.count() == 4


@pytest.mark.django_db
def test_refund_ledgerni_teskari_yozadi(split_booking):
    payment = pay(split_booking)
    r = refunds.request_refund(booking_id=split_booking.id, amount=Decimal("75000"), reason="partial", initiated_by="admin")
    refunds.mark_refund_succeeded(r.id)

    assert ledger.trial_balance() == 0
    assert ledger.balance(A.CLIENT_PAYMENTS) == Decimal("75000.00")
    assert ledger.balance(A.PLATFORM_REVENUE) == Decimal("22500.00") - Decimal("11250.00")
    assert ledger.balance(A.DOCTOR_PAYABLE) == Decimal("126000.00") - Decimal("63750.00")
    payment.refresh_from_db()
    assert payment.status == Payment.Status.PARTIALLY_REFUNDED


@pytest.mark.django_db
def test_ledger_nomutanosib_yozuvni_rad_etadi(split_booking):
    with pytest.raises(ledger.LedgerImbalance):
        ledger._post(kind="x", ref_type="x", ref_id=split_booking.id, booking=split_booking,
                     lines=[(A.CLIENT_PAYMENTS, Decimal("10"), Decimal("0"))])


@pytest.mark.django_db
def test_payme_kabinetdan_bekor_ledgerni_nolga_qaytaradi(split_booking):
    pay(split_booking)
    handle_payme_webhook(method="CancelTransaction", params={"id": "tx-m", "reason": 5})
    assert ledger.balance(A.CLIENT_PAYMENTS) == 0
    assert Refund.objects.get().reason == "provider_cancelled"


# --- B7 reconciliation ------------------------------------------------------


def _tx(p, *, amount=None, state=2, ext=None):
    return TransactionDTO(external_id=ext or p.external_id, amount=amount if amount is not None else p.amount,
                          state=state, booking_id=str(p.booking_id), create_time=0, perform_time=0, cancel_time=0)


def _today():
    return timezone.now().astimezone(reconciliation.LOCAL_TZ).date()


@pytest.mark.django_db
def test_mos_kelsa_farq_yoq(split_booking):
    p = pay(split_booking)
    day = _today()
    assert reconciliation.reconcile_provider("payme", day, [_tx(p)]) == []
    assert reconciliation.reconcile_internal(day) == []


@pytest.mark.django_db
def test_provider_only_amount_status_local_only(split_booking):
    p = pay(split_booking)
    day = _today()
    found = reconciliation.reconcile_provider("payme", day, [
        _tx(p, amount=Decimal("1000")),
        _tx(p, ext="begona-tx", amount=Decimal("5000")),
    ])
    assert {d.type for d in found} == {"amount_mismatch", "provider_only"}

    found = reconciliation.reconcile_provider("payme", day, [_tx(p, state=-2)])
    assert [d.type for d in found] == ["status_mismatch"]

    found = reconciliation.reconcile_provider("payme", day, [])
    assert [d.type for d in found] == ["local_only"]


@pytest.mark.django_db
def test_qayta_ishga_tushsa_dublikat_yoq(split_booking):
    p = pay(split_booking)
    day = _today()
    reconciliation.reconcile_provider("payme", day, [])
    reconciliation.reconcile_provider("payme", day, [])
    assert PaymentDiscrepancy.objects.count() == 1


@pytest.mark.django_db
def test_ichki_tekshiruvlar(split_booking):
    Booking.objects.filter(id=split_booking.id).update(status=Booking.Status.CONFIRMED)
    Payment.objects.filter(booking=split_booking).update(status=Payment.Status.SUCCEEDED, needs_refund=True)
    types = {d.type for d in reconciliation.reconcile_internal(_today())}
    assert types == {"missing_ledger", "unresolved_refund"}

    Payment.objects.filter(booking=split_booking).update(status=Payment.Status.CREATED, needs_refund=False)
    types = {d.type for d in reconciliation.reconcile_internal(_today() + timedelta(days=1))}
    assert types == {"booking_without_payment"}


@pytest.mark.django_db
def test_hal_qilingan_farq_qayta_ochilmaydi(split_booking):
    Booking.objects.filter(id=split_booking.id).update(status=Booking.Status.CONFIRMED)
    reconciliation.reconcile_internal(_today())
    PaymentDiscrepancy.objects.update(status=PaymentDiscrepancy.Status.RESOLVED)
    assert reconciliation.reconcile_internal(_today() + timedelta(days=1)) == []


@pytest.mark.django_db
def test_reestr_fayli_bilan_command(split_booking, tmp_path):
    p = pay(split_booking)
    f = tmp_path / "reestr.csv"
    f.write_text(f"id,amount,state,booking_id\n{p.external_id},{int(p.amount * 100)},2,{p.booking_id}\n", encoding="utf-8")
    call_command("reconcile_payments", "--date", _today().isoformat(), "--file", str(f))
    assert PaymentDiscrepancy.objects.count() == 0


# --- B11 nizolar ------------------------------------------------------------


@pytest.mark.django_db
def test_nizo_ochish_va_mijoz_foydasiga_refund(split_booking):
    from apps.account.models import User

    pay(split_booking)
    client = APIClient()
    client.force_authenticate(split_booking.client)
    resp = client.post(f"/api/v1/booking/bookings/{split_booking.id}/disputes",
                       {"reason": "doctor_no_show", "description": "Kelmadi"}, format="json")
    assert resp.status_code == 201
    dispute_id = resp.json()["id"]
    assert Dispute.objects.get().due_at > timezone.now() + timedelta(hours=47)

    again = client.post(f"/api/v1/booking/bookings/{split_booking.id}/disputes", {"reason": "other"}, format="json")
    assert again.status_code == 409

    assert client.get("/api/v1/booking/disputes").status_code == 403

    admin = APIClient()
    admin.force_authenticate(User.objects.create_user(phone="+998900000009", role="platform_admin"))
    assert len(admin.get("/api/v1/booking/disputes?status=open").json()) == 1
    resp = admin.post(f"/api/v1/booking/disputes/{dispute_id}/resolve",
                      {"in_favor_of": "client", "note": "Tasdiqlandi", "refund_amount": "150000"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved_client"
    assert Refund.objects.get().amount == Decimal("150000.00")


@pytest.mark.django_db
def test_tolanmagan_bronga_nizo_ochilmaydi(pending_booking):
    api = APIClient()
    api.force_authenticate(pending_booking.client)
    resp = api.post(f"/api/v1/booking/bookings/{pending_booking.id}/disputes", {"reason": "other"}, format="json")
    assert resp.status_code == 409
