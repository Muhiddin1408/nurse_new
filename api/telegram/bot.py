"""
telegram/bot.py — shifokorlar uchun Telegram bot MVP (D11).

Ilova yozishdan 10 barobar arzon yo'l: birinchi 100 shifokorga yetadi.

    Bog'lash:  shifokor kabinetida GET /doctor/telegram/link -> t.me/<bot>?start=<token>
               bot /start <token> ni oladi -> Doctor.telegram_chat_id saqlanadi
    /today     bugungi qabullar, har biri [✅ Bo'ldi] [🚫 Kelmadi] tugmalari bilan
    /tomorrow  ertangi qabullar
    /help

XAVFSIZLIK:
  * Webhook `X-Telegram-Bot-Api-Secret-Token` bilan tekshiriladi (Telegram setWebhook secret_token).
  * Bog'lash tokeni imzolangan, 15 daqiqa amal qiladi.
  * Tugma bosilganda bron chat'ga bog'langan shifokorniki ekani qayta tekshiriladi (A4) —
    callback_data soxtalashtirilishi mumkin.
  * Botda bemor telefoni va aniq manzil KO'RSATILMAYDI — Telegram chat tarixi shifokor
    qurilmasidan tashqariga chiqishi mumkin.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.utils import timezone

from api.booking.services import BookingError
from api.doctor import appointments
from api.schedule.services import LOCAL_TZ
from api.telegram import client
from apps.catalog.models import Doctor

logger = logging.getLogger(__name__)

LINK_SALT = "telegram-link"
LINK_TTL_SECONDS = 15 * 60


CLIENT_LINK_SALT = "telegram-client-link"  # C13: mijozlar — alohida tuz (token almashib ketmasin)


def make_client_link(user) -> str:
    token = signing.dumps(str(user.id), salt=CLIENT_LINK_SALT, compress=True)
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"


def _link_client(chat_id: int, token: str) -> bool:
    """C13: mijoz Telegram'i — SMS o'rniga bepul kanal. Token shifokornikidan
    boshqa tuz bilan imzolangan, shuning uchun ular aralashmaydi."""
    from apps.account.models import User
    from apps.notifications.models import NotificationPreference

    try:
        user_id = UUID(signing.loads(token, salt=CLIENT_LINK_SALT, max_age=LINK_TTL_SECONDS))
    except (signing.BadSignature, ValueError):
        return False
    user = User.objects.filter(id=user_id, is_active=True).first()
    if user is None:
        return False
    with transaction.atomic():
        NotificationPreference.objects.filter(telegram_chat_id=chat_id).exclude(user=user).update(telegram_chat_id=None)
        NotificationPreference.objects.update_or_create(user=user, defaults={"telegram_chat_id": chat_id})
    text = ("✅ Telegram подключён. Уведомления о записях будут приходить сюда."
            if user.preferred_language == "ru" else
            "✅ Telegram ulandi. Bron va eslatmalar endi shu yerga keladi (SMS o'rniga).")
    _reply(chat_id, text)
    return True


def make_link(doctor: Doctor) -> str:
    token = signing.dumps(str(doctor.id), salt=LINK_SALT, compress=True)
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"


def _doctor_for_chat(chat_id: int) -> Doctor | None:
    return Doctor.objects.filter(telegram_chat_id=chat_id, status=Doctor.Status.APPROVED).select_related("user").first()


def _reply(chat_id: int, text: str, buttons=None) -> None:
    try:
        client.send_message(chat_id, text, buttons=buttons)
    except client.TelegramError as exc:
        logger.warning("Telegram javob yuborilmadi: chat=%s (%s)", chat_id, exc)


HELP = (
    "MedBron shifokor boti\n\n"
    "/today — bugungi qabullar\n"
    "/tomorrow — ertangi qabullar\n\n"
    "Qabul tugagach ✅ yoki 🚫 tugmasini bosing."
)


def handle_update(update: dict) -> None:
    if "callback_query" in update:
        _handle_callback(update["callback_query"])
        return
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        _link(chat_id, parts[1] if len(parts) > 1 else "")
    elif text.startswith("/today"):
        _send_day(chat_id, 0)
    elif text.startswith("/tomorrow"):
        _send_day(chat_id, 1)
    else:
        _reply(chat_id, HELP)


def _link(chat_id: int, token: str) -> None:
    if not token:
        _reply(chat_id, "Botni ilovadagi «Telegram'ni ulash» havolasi orqali oching.")
        return
    if _link_client(chat_id, token):
        return
    try:
        doctor_id = UUID(signing.loads(token, salt=LINK_SALT, max_age=LINK_TTL_SECONDS))
    except (signing.BadSignature, ValueError):
        _reply(chat_id, "Havola yaroqsiz yoki muddati o'tgan. Kabinetdan yangisini oling.")
        return
    try:
        with transaction.atomic():
            # Bu chat boshqa shifokorga bog'langan bo'lsa — uzamiz (bitta chat — bitta shifokor)
            Doctor.objects.filter(telegram_chat_id=chat_id).exclude(id=doctor_id).update(telegram_chat_id=None)
            updated = Doctor.objects.filter(id=doctor_id).update(telegram_chat_id=chat_id)
    except IntegrityError:
        updated = 0
    if not updated:
        _reply(chat_id, "Bog'lab bo'lmadi. Qayta urinib ko'ring.")
        return
    _reply(chat_id, "✅ Telegram ulandi. Yangi bronlar va eslatmalar shu yerga keladi.\n\n" + HELP)


def _send_day(chat_id: int, offset_days: int) -> None:
    doctor = _doctor_for_chat(chat_id)
    if doctor is None:
        _reply(chat_id, "Bu chat tasdiqlangan shifokorga bog'lanmagan.")
        return
    day = timezone.localtime(timezone.now(), LOCAL_TZ).date() + timedelta(days=offset_days)
    rows = [b for b in appointments.list_range(doctor, day, day, None) if b.status != "cancelled"]
    title = "Bugun" if offset_days == 0 else "Ertaga"
    if not rows:
        _reply(chat_id, f"{title} qabullar yo'q.")
        return
    _reply(chat_id, f"{title}: {len(rows)} ta qabul")
    for b in rows:
        when = b.slot.start_at.astimezone(LOCAL_TZ).strftime("%H:%M")
        services = ", ".join(i.service_name for i in b.items.all())
        place = "🏠 uy chaqiruvi" if b.slot.clinic_id is None else f"🏥 {b.slot.clinic.name}"
        text = f"{when} — {appointments.serialize(b)['patient']['full_name']}\n{services}\n{place}\n#{b.number} · {b.status}"
        buttons = None
        if b.status == "confirmed" and offset_days == 0:
            buttons = [[
                {"text": "✅ Bo'ldi", "callback_data": f"c:{b.id}"},
                {"text": "🚫 Kelmadi", "callback_data": f"n:{b.id}"},
            ]]
        _reply(chat_id, text, buttons)


def _handle_callback(cb: dict) -> None:
    cb_id = cb.get("id", "")
    chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = cb.get("data") or ""
    doctor = _doctor_for_chat(chat_id) if chat_id else None
    if doctor is None:
        client.answer_callback(cb_id, "Ruxsat yo'q")
        return
    try:
        action, raw_id = data.split(":", 1)
        booking_id = UUID(raw_id)
    except ValueError:
        client.answer_callback(cb_id, "Noto'g'ri so'rov")
        return

    try:
        if action == "c":
            appointments.complete(doctor, booking_id, note="")
            answer = "✅ Qabul yakunlandi"
        elif action == "n":
            appointments.no_show(doctor, booking_id, note="")
            answer = "🚫 Mijoz kelmadi deb belgilandi"
        else:
            answer = "Noma'lum amal"
    except appointments.AppointmentError as exc:
        # begona bron — xuddi yo'qdek (A4)
        answer = "Qabul topilmadi" if str(exc) == "not_found" else str(exc)
    except BookingError as exc:
        answer = str(exc)
    client.answer_callback(cb_id, answer)
