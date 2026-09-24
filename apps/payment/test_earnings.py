"""Sprint 2.4 — daromad va payout (D6), shifokorga bildirishnomalar (D9), Telegram bot (D11)."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from api.booking.pricing import split_amount
from api.payments import ledger
from apps.booking.models import Booking, BookingItem
from apps.catalog.models import Doctor
from apps.payment.models import LedgerEntry, Payment, PayoutAccount, PayoutLine, PayoutPeriod, Refund
from apps.schedule.models import TimeSlot
from apps.utils.models import OutboxEvent


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


_counter = {"n": 0}


def done_booking(doctor, clinic, client_user, patient, service, *, days_ago=2, status="completed", no_show_by="",
                 paid=True, price=Decimal("150000")):
    _counter["n"] += 1
    start = (timezone.now() - timedelta(days=days_ago, minutes=40 * _counter["n"])).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30),
                                   status="booked")
    s = split_amount(price, Decimal("0.15"), Decimal("0.01"))
    b = Booking.objects.create(
        number=f"MB-E-{_counter['n']}", client=client_user, patient=patient, doctor=doctor, slot=slot, status=status,
        no_show_by=no_show_by, total_price=price, commission_rate=s.commission_rate,
        provider_fee_rate=s.provider_fee_rate, platform_fee=s.platform_fee, provider_fee=s.provider_fee,
        doctor_payout=s.doctor_payout,
    )
    BookingItem.objects.create(booking=b, service=service, service_name=service.name, price=price, duration_minutes=30)
    if paid:
        p = Payment.objects.create(booking=b, provider="payme", amount=price, status="succeeded",
                                   idempotency_key=f"e-{b.id}")
        ledger.post_payment_succeeded(p.id)
    return b


# --- D6 daromad -------------------------------------------------------------


@pytest.mark.django_db
def test_daromad_xulosasi_faqat_hisoblanadigan_bronlar(doctor, clinic, client_user, patient, service):
    done_booking(doctor, clinic, client_user, patient, service)                                   # hisoblanadi
    done_booking(doctor, clinic, client_user, patient, service, status="no_show", no_show_by="client")  # hisoblanadi
    done_booking(doctor, clinic, client_user, patient, service, status="no_show", no_show_by="doctor")  # yo'q
    done_booking(doctor, clinic, client_user, patient, service, status="cancelled")               # yo'q
    done_booking(doctor, clinic, client_user, patient, service, paid=False)                       # to'lanmagan — yo'q

    d = timezone.localdate()
    data = api_for(doctor.user).get(f"/api/v1/doctor/earnings/summary?from={d - timedelta(days=7)}&to={d}").json()
    assert data["bookings_count"] == 2
    assert Decimal(data["gross"]) == Decimal("300000")
    assert Decimal(data["platform_fee"]) == Decimal("45000")
    assert Decimal(data["provider_fee"]) == Decimal("3000")
    assert Decimal(data["net"]) == Decimal("252000")
    assert Decimal(data["awaiting_payout"]) == Decimal("252000")


@pytest.mark.django_db
def test_refund_shifokor_ulushini_kamaytiradi(doctor, clinic, client_user, patient, service):
    b = done_booking(doctor, clinic, client_user, patient, service)
    Refund.objects.create(payment=b.payments.get(), booking=b, amount=Decimal("50000"), reason="dispute:x",
                          initiated_by="admin", status="succeeded")
    d = timezone.localdate()
    rows = api_for(doctor.user).get(f"/api/v1/doctor/earnings/transactions?from={d - timedelta(days=7)}&to={d}").json()
    line = rows["transactions"][0]
    # 126 000 − (50 000 − 50 000 × 15%) = 126 000 − 42 500
    assert Decimal(line["net"]) == Decimal("83500")
    assert line["patient"].endswith(".") is False


@pytest.mark.django_db
def test_davr_parametrlari(doctor):
    api = api_for(doctor.user)
    for period in ("today", "week", "month"):
        assert api.get(f"/api/v1/doctor/earnings/summary?period={period}").status_code == 200
    assert api.get("/api/v1/doctor/earnings/summary").status_code == 400


# --- D6 payout --------------------------------------------------------------


@pytest.mark.django_db
def test_payout_yaratish_idempotent_va_minimal_summa(doctor, clinic, client_user, patient, service, settings):
    from api.doctor.earnings import build_payouts

    settings.PAYOUT_MIN_AMOUNT = "200000"
    done_booking(doctor, clinic, client_user, patient, service, days_ago=3)
    today = timezone.localdate()
    assert build_payouts(today - timedelta(days=6), today) == []  # 126 000 < 200 000 — keyingi haftaga

    done_booking(doctor, clinic, client_user, patient, service, days_ago=2)
    created = build_payouts(today - timedelta(days=6), today)
    assert len(created) == 1
    p = created[0]
    assert (p.bookings_count, p.net_amount) == (2, Decimal("252000.00"))
    assert build_payouts(today - timedelta(days=6), today) == []  # qayta — yangi narsa yo'q
    assert PayoutLine.objects.count() == 2


@pytest.mark.django_db
def test_payout_tolandi_ledger_va_hisobot(doctor, clinic, client_user, patient, service, settings):
    from api.doctor.earnings import build_payouts
    from apps.account.models import User

    settings.PAYOUT_MIN_AMOUNT = "1"
    done_booking(doctor, clinic, client_user, patient, service)
    today = timezone.localdate()
    payout = build_payouts(today - timedelta(days=6), today)[0]
    admin = api_for(User.objects.create_user(phone="+998900000077", role="platform_admin"))

    url = f"/api/v1/moderation/payouts/{payout.id}/mark-paid"
    assert admin.post(url, {"bank_reference": "PP-1"}, format="json").status_code == 400  # rekvizit yo'q

    doc_api = api_for(doctor.user)
    assert doc_api.put("/api/v1/doctor/payout-account", {"holder_name": "Dr", "account_number": "123"},
                       format="json").status_code == 400
    resp = doc_api.put("/api/v1/doctor/payout-account", {"holder_name": "Dr. Aliyev", "account_number": "8600123412341234"},
                       format="json")
    assert resp.json()["masked_number"] == "•••• 1234"
    assert "account_number" not in resp.json()

    resp = admin.post(url, {"bank_reference": "PP-1"}, format="json")
    assert resp.status_code == 200 and resp.json()["status"] == "paid"
    assert ledger.balance(LedgerEntry.Account.DOCTOR_PAYABLE, doctor_id=doctor.id) == 0
    assert ledger.trial_balance() == 0
    assert OutboxEvent.objects.filter(event_type="PayoutPaid").count() == 1
    admin.post(url, {"bank_reference": "PP-1"}, format="json")  # idempotent
    assert OutboxEvent.objects.filter(event_type="PayoutPaid").count() == 1

    csv_resp = doc_api.get(f"/api/v1/doctor/payouts/{payout.id}/statement")
    body = csv_resp.content.decode("utf-8")
    assert csv_resp["Content-Type"].startswith("text/csv")
    assert body.startswith("﻿") and "JAMI" in body and "126000.00" in body

    summary = doc_api.get(f"/api/v1/doctor/earnings/summary?period=month").json()
    if summary["bookings_count"]:
        assert Decimal(summary["paid_out"]) == Decimal("126000")


@pytest.mark.django_db
def test_begona_payout_hisobotini_olib_bolmaydi(doctor, clinic, client_user, patient, service, settings):
    from api.doctor.earnings import build_payouts
    from apps.account.models import User

    settings.PAYOUT_MIN_AMOUNT = "1"
    done_booking(doctor, clinic, client_user, patient, service)
    today = timezone.localdate()
    payout = build_payouts(today - timedelta(days=6), today)[0]
    other = Doctor.objects.create(user=User.objects.create_user(phone="+998900000078"), status="approved")
    assert api_for(other.user).get(f"/api/v1/doctor/payouts/{payout.id}/statement").status_code == 404


# --- D9 bildirishnomalar ----------------------------------------------------


@pytest.mark.django_db  # C13: shablon matni bazadan ham o'qiladi
def test_shifokorga_telegram_yiqilsa_sms(settings):
    from api.notifications import doctor as dn
    from api.telegram import client

    fake = mock.Mock(telegram_chat_id=555, user=mock.Mock(phone="+998900000001"))
    settings.TELEGRAM_BOT_TOKEN = "t"
    with mock.patch.object(dn, "_contact", return_value=fake), \
            mock.patch.object(client, "send_message", side_effect=client.TelegramError("blocked")), \
            mock.patch("api.notifications.services.send_sms") as sms:
        assert dn.notify_doctor("d", "doctor_daily_summary", {"count": "3", "first": "09:00"}) == "sms"
    sms.assert_called_once()

    with mock.patch.object(dn, "_contact", return_value=fake), mock.patch.object(client, "send_message") as tg:
        assert dn.notify_doctor("d", "doctor_daily_summary", {"count": "3", "first": "09:00"}) == "telegram"
    assert "3 ta qabul" in tg.call_args.args[1]


# C10 dan keyin bu ishlovchi bazaga tegadi: bo'shagan vaqt navbatdagi
# odamga taklif qilinadi (navbat bo'sh bo'lsa ham so'rov ketadi).
@pytest.mark.django_db
def test_tolanmagan_bron_bekor_bolsa_shifokor_bezovta_qilinmaydi():
    from api.notifications import consumer

    # `doctor_id` haqiqiy UUID: C10 navbat so'rovi u bilan bazaga boradi
    data = {"client_phone": "+998", "booking_number": "MB-1",
            "doctor_id": "8b0e7b0e-0000-4000-8000-000000000002",
            "start_at": "2026-09-20T09:00:00+00:00",
            "previous_status": "pending_payment"}
    with mock.patch("api.notifications.services.send_sms"), \
            mock.patch("api.notifications.doctor.notify_doctor") as notify:
        consumer._handle_booking_cancelled(data)
        notify.assert_not_called()
        consumer._handle_booking_cancelled({**data, "previous_status": "confirmed"})
        notify.assert_called_once()
        assert notify.call_args.args[1] == "doctor_booking_cancelled"


@pytest.mark.django_db
def test_hodisa_payloadida_vaqt_va_oldingi_holat(pending_booking):
    from api.booking.services import cancel_booking

    cancel_booking(pending_booking.id)
    ev = OutboxEvent.objects.get(event_type="BookingCancelled")
    assert ev.payload["previous_status"] == "pending_payment"
    assert ev.payload["start_at"]


@pytest.mark.django_db
def test_ertalabki_xulosa_kuniga_bir_marta(doctor, clinic, client_user, patient, service):
    from api.doctor.digest import send_daily_summaries
    from django.core.cache import cache

    cache.clear()
    from datetime import datetime, time

    from api.schedule.services import LOCAL_TZ, today_local

    start = datetime.combine(today_local(), time(23, 30), tzinfo=LOCAL_TZ)
    if start <= timezone.now():
        pytest.skip("Toshkentda kun oxiri")
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))
    Booking.objects.create(number="MB-D", client=client_user, patient=patient, doctor=doctor, slot=slot,
                           status="confirmed", total_price=1)
    with mock.patch("api.doctor.digest.notify_doctor") as notify:
        assert send_daily_summaries() == 1
        assert send_daily_summaries() == 0
    assert notify.call_args.args[2]["count"] == "1"


# --- D11 Telegram -----------------------------------------------------------


@pytest.fixture
def tg(settings):
    settings.TELEGRAM_BOT_TOKEN = "123:abc"
    settings.TELEGRAM_WEBHOOK_SECRET = "s3cret"
    with mock.patch("api.telegram.client.send_message") as send, \
            mock.patch("api.telegram.client.answer_callback") as answer:
        yield send, answer


def hook(update, secret="s3cret"):
    return APIClient().post("/api/v1/telegram/webhook", update, format="json",
                            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret)


@pytest.mark.django_db
def test_webhook_sekretsiz_403(tg):
    assert hook({"message": {}}, secret="wrong").status_code == 403


@pytest.mark.django_db
def test_telegram_boglash_today_va_tugma(tg, doctor, clinic, client_user, patient, service):
    send, answer = tg
    link = api_for(doctor.user).get("/api/v1/doctor/telegram/link").json()
    token = link["url"].split("start=")[1]

    assert hook({"message": {"chat": {"id": 777}, "text": f"/start {token}"}}).status_code == 200
    doctor.refresh_from_db()
    assert doctor.telegram_chat_id == 777

    start = (timezone.now() - timedelta(minutes=5)).replace(second=0, microsecond=0)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30),
                                   status="booked")
    b = Booking.objects.create(number="MB-TG", client=client_user, patient=patient, doctor=doctor, slot=slot,
                               status="confirmed", total_price=1)
    BookingItem.objects.create(booking=b, service=service, service_name=service.name, price=1, duration_minutes=30)

    send.reset_mock()
    hook({"message": {"chat": {"id": 777}, "text": "/today"}})
    texts = [c.args[1] for c in send.call_args_list]
    if timezone.localtime(start).date() == timezone.localdate():
        assert any("MB-TG" in t for t in texts)
        assert client_user.phone not in " ".join(texts)  # maxfiylik

    hook({"callback_query": {"id": "cb1", "data": f"c:{b.id}", "message": {"chat": {"id": 777}}}})
    b.refresh_from_db()
    assert b.status == "completed"
    assert "yakunlandi" in answer.call_args.args[1]


@pytest.mark.django_db
def test_telegram_begona_chat_va_soxta_token(tg, doctor, clinic, client_user, patient, service):
    send, answer = tg
    hook({"message": {"chat": {"id": 1}, "text": "/start soxta"}})
    assert "yaroqsiz" in send.call_args.args[1]

    start = timezone.now() - timedelta(minutes=5)
    slot = TimeSlot.objects.create(doctor=doctor, clinic=clinic, start_at=start, end_at=start + timedelta(minutes=30))
    b = Booking.objects.create(number="MB-X", client=client_user, patient=patient, doctor=doctor, slot=slot,
                               status="confirmed", total_price=1)
    # bog'lanmagan chat boshqa shifokor bronini yakunlay olmaydi
    hook({"callback_query": {"id": "cb", "data": f"c:{b.id}", "message": {"chat": {"id": 999}}}})
    b.refresh_from_db()
    assert b.status == "confirmed"
    assert answer.call_args.args[1] == "Ruxsat yo'q"
