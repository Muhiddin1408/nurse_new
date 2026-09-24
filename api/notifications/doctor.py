"""
notifications/doctor.py — shifokorga bildirishnomalar (D9).

Kanal ustuvorligi: push (ilova, keyinroq) > Telegram (bepul) > SMS (pullik, zaxira).
Telegram yiqilsa — SMS ga tushadi: shifokor yangi bron haqida bilmasa, tizim ishlamaydi.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from api.notifications import services as sms_services
from api.notifications.templates import render_template
from api.schedule.services import LOCAL_TZ
from api.telegram import client as telegram

logger = logging.getLogger(__name__)


def _contact(doctor_id: UUID):
    from apps.catalog.models import Doctor

    return Doctor.objects.filter(id=doctor_id).select_related("user").first()


def notify_doctor(doctor_id: UUID, template: str, params: dict) -> str:
    """Qaytaradi: 'telegram' | 'sms' | 'skipped'."""
    doctor = _contact(doctor_id)
    if doctor is None:
        return "skipped"

    if doctor.telegram_chat_id and telegram.is_enabled():
        try:
            telegram.send_message(doctor.telegram_chat_id, render_template(template, "uz", params))
            return "telegram"
        except telegram.TelegramError as exc:
            logger.warning("Telegram yuborilmadi, SMS ga o'tildi: doctor=%s (%s)", doctor_id, exc)

    sms_services.send_sms(phone=doctor.user.phone, template=template, params=params)
    return "sms"


def fmt_when(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")
