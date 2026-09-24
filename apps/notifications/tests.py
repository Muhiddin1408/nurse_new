"""SMS provayderlari. Eskiz'ga haqiqiy so'rov ketmaydi — `requests.post` mock qilinadi."""

from unittest import mock

import pytest
import requests
from django.core.cache import cache
from django.test import override_settings

from api.notifications.sms import EskizSmsClient, SmsSendError

ESKIZ = dict(
    SMS_PROVIDER="eskiz",
    ESKIZ_BASE_URL="https://eskiz.test",
    ESKIZ_EMAIL="a@b.uz",
    ESKIZ_PASSWORD="secret",
    ESKIZ_FROM="4546",
)


def _resp(status, json=None, text=""):
    r = mock.Mock(status_code=status, text=text)
    r.json.return_value = json or {}
    return r


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@override_settings(**ESKIZ)
def test_eskiz_login_qilib_sms_yuboradi_va_tokenni_keshlaydi():
    with mock.patch("api.notifications.sms.requests.post") as post:
        post.side_effect = [
            _resp(200, {"data": {"token": "T1"}}),
            _resp(200, {"id": "m-1", "status": "waiting"}),
            _resp(200, {"id": "m-2", "status": "waiting"}),
        ]
        client = EskizSmsClient()
        assert client.send("+998901234567", "salom") == "m-1"
        assert client.send("+998901234567", "salom") == "m-2"

    # login bir marta, keyin keshdagi token
    assert post.call_count == 3
    sms_call = post.call_args_list[1]
    assert sms_call.args[0] == "https://eskiz.test/api/message/sms/send"
    assert sms_call.kwargs["headers"] == {"Authorization": "Bearer T1"}
    assert sms_call.kwargs["data"]["mobile_phone"] == "998901234567"


@override_settings(**ESKIZ)
def test_eskiz_401_da_qayta_login_qiladi():
    cache.set(EskizSmsClient.TOKEN_CACHE_KEY, "ESKI")
    with mock.patch("api.notifications.sms.requests.post") as post:
        post.side_effect = [
            _resp(401, text="Expired"),
            _resp(200, {"data": {"token": "YANGI"}}),
            _resp(200, {"id": "m-1"}),
        ]
        assert EskizSmsClient().send("+998901234567", "salom") == "m-1"

    assert cache.get(EskizSmsClient.TOKEN_CACHE_KEY) == "YANGI"


@override_settings(**ESKIZ)
def test_eskiz_xato_va_tarmoq_uzilishi_SmsSendError():
    cache.set(EskizSmsClient.TOKEN_CACHE_KEY, "T")
    with mock.patch("api.notifications.sms.requests.post") as post:
        post.return_value = _resp(400, text="Text not moderated")
        with pytest.raises(SmsSendError):
            EskizSmsClient().send("+998901234567", "salom")

        post.side_effect = requests.ConnectionError("down")
        with pytest.raises(SmsSendError):
            EskizSmsClient().send("+998901234567", "salom")


@pytest.mark.django_db
@override_settings(**ESKIZ)
def test_bildirishnoma_sms_xatosi_yiqitmaydi_va_loglanadi():
    from api.notifications.services import send_sms
    from apps.notifications.models import SmsLog

    with mock.patch("api.notifications.sms.requests.post", side_effect=requests.Timeout("t")):
        send_sms(phone="+998901234567", template="booking_confirmed", params={"number": "MB-1"})

    assert SmsLog.objects.get().status == SmsLog.Status.FAILED


@pytest.mark.django_db
@override_settings(**ESKIZ)
def test_otp_sms_ketmasa_503_va_cooldown_sarflanmaydi():
    from rest_framework.test import APIClient

    from apps.account.models import OtpCode

    url = "/api/v1/accounts/auth/otp/request"
    with mock.patch("api.notifications.sms.requests.post", side_effect=requests.Timeout("t")):
        resp = APIClient().post(url, {"phone": "+998901234567"}, format="json")

    assert resp.status_code == 503
    assert OtpCode.objects.count() == 0

    cache.clear()  # DRF throttle hisobini tozalash
    with mock.patch("api.notifications.sms.requests.post") as post:
        post.side_effect = [_resp(200, {"data": {"token": "T"}}), _resp(200, {"id": "m-1"})]
        resp = APIClient().post(url, {"phone": "+998901234567"}, format="json")

    assert resp.status_code == 200
    assert "MedBron tasdiqlash kodi" in post.call_args.kwargs["data"]["message"]
