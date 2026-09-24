"""C13 kanallar va C14 tillar."""

from unittest import mock

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from api.notifications import services
from apps.notifications.models import NotificationPreference, NotificationTemplate, PushDevice


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


def _notify(user, **kw):
    return services.notify(phone=user.phone, template="booking_confirmed", params={"number": "1"}, **kw)


# ---------------------------------------------------------------------------
# C13 — kanal ustuvorligi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(PUSH_PROVIDER="console")
def test_push_bor_bolsa_sms_ketmaydi(client_user):
    PushDevice.objects.create(user=client_user, token="tok-1", platform="android")
    with mock.patch.object(services, "send_sms") as sms:
        assert _notify(client_user) == "push"
    sms.assert_not_called()


@pytest.mark.django_db
@override_settings(PUSH_PROVIDER="off", TELEGRAM_BOT_TOKEN="t")
def test_telegram_yiqilsa_sms_zaxira(client_user):
    from api.telegram import client as telegram

    NotificationPreference.objects.create(user=client_user, telegram_chat_id=77, sms_enabled=False)
    with mock.patch.object(telegram, "send_message", side_effect=telegram.TelegramError("x")), \
            mock.patch.object(services, "send_sms") as sms:
        # SMS o'chirilgan, lekin tranzaksion xabar baribir yetkazilishi shart
        assert _notify(client_user) == "sms"
    sms.assert_called_once()
    with mock.patch.object(telegram, "send_message") as tg, mock.patch.object(services, "send_sms") as sms:
        assert _notify(client_user) == "telegram"
    assert "1" in tg.call_args.args[1]
    sms.assert_not_called()


@pytest.mark.django_db
def test_marketing_faqat_rozilik_bilan_va_smssiz(client_user):
    with mock.patch.object(services, "send_sms") as sms:
        assert _notify(client_user, marketing=True) == "skipped"
        NotificationPreference.objects.create(user=client_user, marketing_opt_in=True)
        assert _notify(client_user, marketing=True) == "skipped"  # push/telegram yo'q, SMS'ga tushmaydi
    sms.assert_not_called()


@pytest.mark.django_db
@override_settings(PUSH_PROVIDER="fcm")
def test_yaroqsiz_push_token_faolsizlanadi(client_user):
    from api.notifications import push

    d = PushDevice.objects.create(user=client_user, token="old", platform="ios")
    with mock.patch.object(push, "send", side_effect=push.InvalidToken("UNREGISTERED")), \
            mock.patch.object(services, "send_sms") as sms:
        assert _notify(client_user) == "sms"
    assert not PushDevice.objects.get(id=d.id).is_active
    sms.assert_called_once()


@pytest.mark.django_db
def test_shablon_bazadan_va_mijoz_tilida(client_user):
    from django.core.cache import cache

    client_user.preferred_language = "ru"
    client_user.save()
    with mock.patch.object(services, "send_sms") as sms:
        _notify(client_user)
    assert sms.call_args.kwargs["language"] == "ru"

    NotificationTemplate.objects.create(key="booking_confirmed", language="ru", text="Запись #{number} подтверждена!")
    cache.clear()
    from api.notifications.templates import render_template

    assert render_template("booking_confirmed", "ru", {"number": "7"}) == "Запись #7 подтверждена!"
    assert render_template("booking_confirmed", "uz", {"number": "7"}).startswith("MedBron")


@pytest.mark.django_db
def test_admin_shablonda_notanish_parametr_rad_etiladi():
    from apps.notifications.admin import NotificationTemplateForm

    bad = NotificationTemplateForm(data={"key": "booking_confirmed", "language": "ru",
                                         "text": "{number} {secret}", "is_active": True})
    assert not bad.is_valid()


# ---------------------------------------------------------------------------
# Sozlamalar endpointlari
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sozlamalar_va_push_qurilma(client_user):
    from apps.utils.models import AuditLog

    api = api_for(client_user)
    assert api.get("/api/v1/accounts/me/preferences").json()["language"] == "uz"
    r = api.patch("/api/v1/accounts/me/preferences", {"language": "ru", "marketing_opt_in": True}, format="json")
    assert r.json()["language"] == "ru" and r.json()["marketing_opt_in"] is True
    assert AuditLog.objects.filter(action="user.marketing_consent").exists()

    assert api.post("/api/v1/accounts/me/push-devices", {"token": "abc", "platform": "android"},
                    format="json").status_code == 204
    assert PushDevice.objects.get(token="abc").user == client_user
    assert api.delete("/api/v1/accounts/me/push-devices", {"token": "abc"}, format="json").status_code == 204
    assert not PushDevice.objects.exists()


@pytest.mark.django_db
@override_settings(TELEGRAM_BOT_TOKEN="t")
def test_mijoz_telegram_boglash(client_user):
    from api.telegram import bot

    url = api_for(client_user).get("/api/v1/accounts/me/telegram/link").json()["url"]
    token = url.split("start=")[1]
    with mock.patch.object(bot, "_reply"):
        bot.handle_update({"message": {"chat": {"id": 4242}, "text": f"/start {token}"}})
    assert NotificationPreference.objects.get(user=client_user).telegram_chat_id == 4242


# ---------------------------------------------------------------------------
# C14 — API tili
# ---------------------------------------------------------------------------

_BODY = {"doctor_id": "00000000-0000-0000-0000-000000000001",
         "service_ids": ["00000000-0000-0000-0000-000000000002"]}


@pytest.mark.django_db
def test_xato_xabari_ruscha(client_user):
    api = api_for(client_user)
    uz = api.post("/api/v1/booking/price-preview", _BODY, format="json")
    ru = api.post("/api/v1/booking/price-preview", _BODY, format="json", HTTP_ACCEPT_LANGUAGE="ru")
    assert uz.json()["detail"] == "Ba'zi xizmatlar topilmadi yoki faol emas"
    assert ru.json()["detail"] == "Некоторые услуги не найдены или неактивны"
    assert ru["Content-Language"] == "ru"
    q = api.post("/api/v1/booking/price-preview?lang=ru", _BODY, format="json")
    assert q.json()["detail"].startswith("Некоторые")


def test_parametrli_xabar_tarjimasi():
    from api.i18n import translate

    assert translate("Bu xizmatlar uchun 60 daqiqa kerak, tanlangan vaqt 30 daqiqa", "ru") == \
        "Для этих услуг нужно 60 мин., выбранное время — 30 мин."
    assert translate("Terapevt: bu shifokor bolalarni qabul qilmaydi", "ru") == "Terapevt: врач не принимает детей"
    assert translate("Noma'lum matn", "ru") == "Noma'lum matn"


def test_foydalanuvchi_xabarlari_katalogda():
    """Kodga yangi o'zbekcha xato xabari qo'shilsa va tarjimasi unutilsa — bu test aytadi."""
    import ast
    import pathlib

    from api.i18n import RU, RU_PATTERNS

    # Ichki / texnik xabarlar — foydalanuvchiga ko'rinmaydi yoki tarjima shart emas
    internal = ("Eskiz", "Telegram", "Click", "Imzo", "Idempotency", "broker", "not_found", "booking_id",
                "Ichki xato", "Tranzaksiya", "Buyurtma", "So'rov", "Summa", "action", "params", "client_phone",
                "2FA sozlanmagan", "Bron raqami", "FCM", "Push", "google-auth")
    root = pathlib.Path(__file__).resolve().parents[2] / "api"
    missing = []
    for p in root.rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Dict):
                values = [v for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == "detail"]
            elif isinstance(n, ast.Call) and (getattr(n.func, "id", "") or getattr(n.func, "attr", "")).endswith(
                    ("Error", "Request", "Invalid", "Blocked", "NotFound")):
                values = n.args
            else:
                continue
            for v in values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    text = v.value
                    if text in RU or text.startswith(internal) or any(pt.match(text) for pt, _ in RU_PATTERNS):
                        continue
                    missing.append(f"{p.name}: {text}")
    assert not missing, "Ruscha tarjimasi yo'q:\n" + "\n".join(sorted(set(missing)))


@pytest.mark.django_db
def test_katalog_nomlari_tilga_qarab(specialization, service, doctor):
    specialization.name_ru = "Терапевт"
    specialization.save()
    service.name_ru = "Консультация"
    service.save()
    api = APIClient()
    assert api.get("/api/v1/catalog/specializations", HTTP_ACCEPT_LANGUAGE="ru").json()[0]["name"] == "Терапевт"
    assert api.get("/api/v1/catalog/specializations").json()[0]["name"] == "Terapevt"
    svc = api.get(f"/api/v1/catalog/doctors/{doctor.id}/services", HTTP_ACCEPT_LANGUAGE="ru").json()
    assert svc[0]["name"] == "Консультация"
