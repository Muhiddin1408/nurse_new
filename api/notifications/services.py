import logging

from apps.notifications.models import SmsLog
from api.notifications.sms import get_sms_client
from api.notifications.templates import render_template

logger = logging.getLogger(__name__)
def send_sms(*, phone: str, template: str, params: dict[str, str], language: str = "uz") -> None:
    """consumer.py chaqiradigan asosiy funksiya.

    ATAYLAB XATO KO'TARMAYDI (fail-safe): agar SMS ketmasa, buni SmsLog'ga
    yozamiz va monitoringga qoldiramiz, lekin Kafka consumer'ni yiqitmaymiz.
    Aks holda bitta yomon telefon raqami butun consumer'ni to'xtatib qo'yadi
    va undan keyingi barcha SMS'lar navbatda kutib qoladi (head-of-line blocking).
    """
    try:
        text = render_template(template, language, params)
    except ValueError:
        logger.exception("Shablon xatosi: %s", template)
        SmsLog.objects.create(phone=phone, template=template, status=SmsLog.Status.FAILED, error="template_error")
        return

    provider = SmsProvider()
    try:
        message_id = provider.send(phone, text)
        SmsLog.objects.create(
            phone=phone,
            template=template,
            status=SmsLog.Status.SENT,
            provider_message_id=message_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("SMS yuborilmadi: phone=%s, xato=%s", _mask(phone), exc)
        SmsLog.objects.create(
            phone=phone, template=template, status=SmsLog.Status.FAILED, error=str(exc)[:2000]
        )
        # ⚠️ Faza 7 da: bu yerga Prometheus counter qo'shiladi
        # (`sms_send_failures_total`) va alert o'rnatiladi.


class SmsProvider:
    """Tashqi SMS API bilan yagona aloqa nuqtasi.

    Provayderni almashtirish kerak bo'lsa (Eskiz -> Play Mobile), faqat
    shu klass o'zgaradi — send_sms va undan yuqorisi tegilmaydi.
    """

    def send(self, phone: str, text: str) -> str:
        # Qaysi provayder — settings.SMS_PROVIDER (api/notifications/sms.py)
        return get_sms_client().send(phone, text)


def _mask(phone: str) -> str:
    return phone[:-6] + "******" if len(phone) > 6 else "***"


# ---------------------------------------------------------------------------
# C13: kanal tanlash — push > Telegram > SMS
# ---------------------------------------------------------------------------

PUSH_TITLES = {"uz": "MedBron", "ru": "MedBron"}


def notify(*, phone: str, template: str, params: dict[str, str], marketing: bool = False,
           language: str | None = None) -> str:
    """Mijozga xabar — eng arzon ishlaydigan kanal orqali. Qaytaradi: push | telegram | sms | skipped.

    Tranzaksion xabar (bron, to'lov, eslatma) YETKAZILISHI shart: bepul kanallar
    ishlamasa SMS ketadi — foydalanuvchi SMS'ni o'chirgan bo'lsa ham (boshqa yo'l yo'q).
    Marketing — faqat rozilik bilan va SMS'siz (pullik va bezovta qiluvchi).
    Xato KO'TARMAYDI (send_sms bilan bir xil fail-safe qoida).
    """
    from apps.account.models import User

    user = User.objects.filter(phone=phone, is_active=True).select_related("notification_pref").first()
    pref = getattr(user, "notification_pref", None) if user else None
    lang = language or (user.preferred_language if user else "uz")

    if marketing and not (pref and pref.marketing_opt_in):
        return "skipped"

    try:
        text = render_template(template, lang, params)
    except ValueError:
        logger.exception("Shablon xatosi: %s", template)
        return "skipped"

    if user and (pref is None or pref.push_enabled) and _send_push(user, text):
        return "push"
    if pref and pref.telegram_enabled and pref.telegram_chat_id and _send_telegram(pref.telegram_chat_id, text):
        return "telegram"
    if marketing:
        return "skipped"
    send_sms(phone=phone, template=template, params=params, language=lang)
    return "sms"


def _send_push(user, text: str) -> bool:
    from api.notifications import push
    from apps.notifications.models import PushDevice

    if not push.is_enabled():
        return False
    delivered = False
    for device in PushDevice.objects.filter(user=user, is_active=True):
        try:
            push.send(device.token, PUSH_TITLES.get(user.preferred_language, "MedBron"), text)
            delivered = True
        except push.InvalidToken:
            PushDevice.objects.filter(id=device.id).update(is_active=False)
        except push.PushError as exc:
            logger.warning("Push yuborilmadi: device=%s (%s)", device.id, exc)
    return delivered


def _send_telegram(chat_id: int, text: str) -> bool:
    from api.telegram import client as telegram

    if not telegram.is_enabled():
        return False
    try:
        telegram.send_message(chat_id, text)
        return True
    except telegram.TelegramError as exc:
        logger.warning("Telegram yuborilmadi: chat=%s (%s)", chat_id, exc)
        return False
