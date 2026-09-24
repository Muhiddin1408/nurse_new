"""Sprint 3.3 — Xavfsizlikni yopish: A7 throttle, A10 PII, A11 audit, A12 admin 2FA/IP, A14 OTP."""

from datetime import timedelta
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from api import totp
from apps.account.models import AdminTOTPDevice, OtpCode, Patient, User
from apps.booking.models import Booking
from apps.utils.models import AuditLog, OutboxEvent


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


# ---------------------------------------------------------------------------
# A7 — throttle
# ---------------------------------------------------------------------------


def _rates(**over):
    from django.conf import settings

    rf = dict(settings.REST_FRAMEWORK)
    rf["DEFAULT_THROTTLE_RATES"] = {**rf["DEFAULT_THROTTLE_RATES"], **over}
    return rf


@pytest.mark.django_db
def test_yozuv_endpointi_foydalanuvchi_boyicha_cheklanadi(client_user):
    from rest_framework.throttling import SimpleRateThrottle

    rates = _rates(write="2/hour")["DEFAULT_THROTTLE_RATES"]
    with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
        api = api_for(client_user)
        codes = [api.post("/api/v1/patient/patients/", {}, format="json").status_code for _ in range(3)]
    assert codes[-1] == 429
    assert 429 not in codes[:2]


@pytest.mark.django_db
def test_bron_yaratish_alohida_limit(client_user):
    from rest_framework.throttling import SimpleRateThrottle

    rates = _rates(booking_create="1/hour")["DEFAULT_THROTTLE_RATES"]
    with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
        api = api_for(client_user)
        first = api.post("/api/v1/booking/bookings", {}, format="json").status_code
        second = api.post("/api/v1/booking/bookings", {}, format="json").status_code
        # boshqa scope — hali ochiq
        other = api.post("/api/v1/patient/patients/", {}, format="json").status_code
    assert first == 400 and second == 429 and other != 429


@pytest.mark.django_db
def test_anonim_oqish_ip_boyicha_cheklanadi():
    from rest_framework.throttling import SimpleRateThrottle

    rates = _rates(anon_read="2/hour")["DEFAULT_THROTTLE_RATES"]
    with mock.patch.object(SimpleRateThrottle, "THROTTLE_RATES", rates):
        api = APIClient()
        codes = [api.get("/api/v1/catalog/specializations").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------------------
# A10 — PII
# ---------------------------------------------------------------------------


def test_client_hash_barqaror_va_raqamni_oshkor_qilmaydi():
    from api.pii import client_hash

    h = client_hash("+998901234567")
    assert h == client_hash("+998901234567") and len(h) == 32
    assert "901234567" not in h
    with override_settings(ANALYTICS_SALT="boshqa"):
        assert client_hash("+998901234567") != h


@pytest.mark.django_db
def test_sms_consumer_raqamni_bazadan_oladi(pending_booking):
    from api.notifications import consumer

    with mock.patch("api.notifications.services.send_sms") as send:
        consumer._handle_booking_confirmed({"booking_id": str(pending_booking.id), "booking_number": "MB-1",
                                            "start_at": None})
    assert send.call_args.kwargs["phone"] == pending_booking.client.phone


@pytest.mark.django_db
def test_akkaunt_ochirish_anonimlashtiradi(client_user, patient):
    api = api_for(client_user)
    assert api.post("/api/v1/accounts/me/delete").status_code == 204
    client_user.refresh_from_db()
    assert client_user.phone.startswith("deleted-") and not client_user.is_active
    assert Patient.objects.get(id=patient.id).full_name == "O'chirilgan"
    assert AuditLog.objects.filter(action="user.delete").exists()


@pytest.mark.django_db
def test_faol_broni_bor_akkaunt_ochirilmaydi(pending_booking):
    assert api_for(pending_booking.client).post("/api/v1/accounts/me/delete").status_code == 409


@pytest.mark.django_db
def test_muddati_otgan_malumot_tozalanadi():
    from apps.utils.management.commands.purge_expired_data import purge

    old = OtpCode.objects.create(phone="+998900000001", code_hash="x", expires_at=timezone.now())
    OtpCode.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(days=30))
    fresh = OtpCode.objects.create(phone="+998900000002", code_hash="x", expires_at=timezone.now())
    ev = OutboxEvent.objects.create(topic="t", key="k", event_type="E", payload={},
                                    status=OutboxEvent.Status.PUBLISHED)
    OutboxEvent.objects.filter(id=ev.id).update(created_at=timezone.now() - timedelta(days=30))

    result = purge()
    assert result["otp_codes"] == 1 and result["outbox"] == 1
    assert OtpCode.objects.filter(id=fresh.id).exists()


# ---------------------------------------------------------------------------
# A11 — audit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_audit_log_ozgarmas():
    row = AuditLog.objects.create(action="x", object_type="t", object_id="1")
    with pytest.raises(PermissionError):
        row.save()
    with pytest.raises(PermissionError):
        row.delete()
    with pytest.raises(PermissionError):
        AuditLog.objects.filter(id=row.id).update(action="y")
    with pytest.raises(PermissionError):
        AuditLog.objects.all().delete()


@pytest.mark.django_db
def test_admin_bron_yakunlashi_actor_va_ip_bilan_yoziladi(pending_booking):
    from api.booking import services as bs

    bs.confirm_booking(pending_booking.id)
    Booking.objects.filter(id=pending_booking.id).update(started_at=timezone.now())
    from apps.schedule.models import TimeSlot

    TimeSlot.objects.filter(id=pending_booking.slot_id).update(start_at=timezone.now() - timedelta(hours=1))
    admin = User.objects.create_user(phone="+998900000055", role="platform_admin")
    r = api_for(admin).post(f"/api/v1/booking/bookings/{pending_booking.id}/complete", {}, format="json",
                            REMOTE_ADDR="10.1.2.3")
    assert r.status_code == 200, r.content
    log = AuditLog.objects.get(action="booking.complete")
    assert (log.actor_id, log.actor_role, log.ip) == (admin.id, "platform_admin", "10.1.2.3")
    assert log.before == {"status": "confirmed"}


@pytest.mark.django_db
def test_rol_ozgarishi_auditga_tushadi(client_user):
    client_user.role = "platform_admin"
    client_user.save()
    log = AuditLog.objects.get(action="user.role_change")
    assert log.before == {"role": "client"} and log.after == {"role": "platform_admin"}


# ---------------------------------------------------------------------------
# A12 — admin
# ---------------------------------------------------------------------------


def test_totp_rfc6238_vektori():
    # RFC 6238 ilova B: secret "12345678901234567890", T=59 -> 94287082 (8 raqam) -> 287082
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59 // 30) == "287082"
    step = totp.verify(secret, "287082", now=59)
    assert step == 1
    assert totp.verify(secret, "287082", now=59, last_step=step) is None  # qayta ishlatib bo'lmaydi
    assert totp.verify(secret, "000000", now=59) is None


@pytest.fixture
def staff(db):
    return User.objects.create_superuser(phone="+998900000066", password="Kuchli-parol-123")


@pytest.mark.django_db
@override_settings(ADMIN_2FA_REQUIRED=True)
def test_admin_login_2fa_siz_otmaydi(client, staff):
    r = client.post("/admin/login/", {"username": staff.phone, "password": "Kuchli-parol-123"})
    assert r.status_code == 200 and "2FA sozlanmagan" in r.content.decode()

    secret = totp.new_secret()
    AdminTOTPDevice.objects.create(user=staff, secret=secret)
    bad = client.post("/admin/login/", {"username": staff.phone, "password": "Kuchli-parol-123", "otp_token": "000000"})
    assert "2FA kodi noto" in bad.content.decode()

    code = totp.code_at(secret, totp.current_step())
    ok = client.post("/admin/login/", {"username": staff.phone, "password": "Kuchli-parol-123", "otp_token": code})
    assert ok.status_code == 302


@pytest.mark.django_db
@override_settings(ADMIN_ALLOWED_IPS=["10.0.0.0/8"])
def test_admin_ruxsatsiz_ipdan_404(client):
    assert client.get("/admin/login/", REMOTE_ADDR="8.8.8.8").status_code == 404
    assert client.get("/admin/login/", REMOTE_ADDR="10.5.5.5").status_code == 200


# ---------------------------------------------------------------------------
# A14 — OTP
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_otp_faqat_ozbekiston_raqamlari():
    api = APIClient()
    r = api.post("/api/v1/accounts/auth/otp/request", {"phone": "+447911123456"}, format="json")
    assert r.status_code == 400 and "+998" in r.json()["detail"]
    assert not OtpCode.objects.exists()


@pytest.mark.django_db
def test_otp_verify_raqam_boyicha_bloklanadi():
    from api.account import services as acc

    phone = "+998901112299"
    for _ in range(acc.MAX_FAILED_VERIFY_PER_PHONE_HOUR):
        with pytest.raises(acc.InvalidCode):
            acc.verify_otp(phone, "000000")
    with pytest.raises(acc.TooManyRequests):
        acc.verify_otp(phone, "000000")
    r = APIClient().post("/api/v1/accounts/auth/otp/verify", {"phone": phone, "code": "123456"}, format="json")
    assert r.status_code == 429
