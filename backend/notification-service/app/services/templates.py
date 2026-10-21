"""Xabar matnlari (uz/ru). Koddagi standart + admin o'zgartirgan matn (bazada, 60 s kesh).

⚠️ Eskiz'da SMS matni moderatsiyadan o'tgan bo'lishi SHART — yangi/o'zgargan matnni Eskiz kabinetida
tasdiqlating, aks holda SMS rad etiladi (push/Telegram'ga ta'sir qilmaydi).
"""

from __future__ import annotations

import string
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Template

LOCAL_TZ = ZoneInfo("Asia/Tashkent")

DEFAULTS: dict[str, dict[str, str]] = {
    # --- mijoz: bron --------------------------------------------------------------------------
    "booking_confirmed": {
        "uz": "MedBron: buyurtmangiz #{number} tasdiqlandi — {when}. {place}.",
        "ru": "MedBron: ваш заказ #{number} подтверждён — {when}. {place}.",
    },
    "booking_cancelled": {
        "uz": "MedBron: buyurtma #{number} bekor qilindi.",
        "ru": "MedBron: заказ #{number} отменён.",
    },
    # `booking_cancelled` dan ATAYLAB boshqa matn: sabab — to'lov ulgurmadi, yechim — qayta bron qilish.
    "booking_expired": {
        "uz": "MedBron: buyurtma #{number} to'lov qilinmagani uchun bekor bo'ldi. Qayta bron qilishingiz mumkin.",
        "ru": "MedBron: заказ #{number} отменён — оплата не поступила вовремя. Вы можете забронировать заново.",
    },
    "booking_rescheduled": {
        "uz": "MedBron: buyurtma #{number} yangi vaqtga ko'chirildi — {when}.",
        "ru": "MedBron: заказ #{number} перенесён на {when}.",
    },
    "booking_reminder_24h": {
        "uz": "MedBron: ertaga {when} da qabul. {place}. Bekor qilish yoki ko'chirish — ilovada.",
        "ru": "MedBron: завтра в {when} приём. {place}. Отменить или перенести — в приложении.",
    },
    "booking_reminder_2h": {
        "uz": "MedBron: 2 soatdan keyin qabul — {when}. {place}. Kechiksangiz oldindan xabar bering.",
        "ru": "MedBron: приём через 2 часа — {when}. {place}. Предупредите, если опаздываете.",
    },
    "booking_review_request": {
        "uz": "MedBron: qabul qanday o'tdi? Shifokorni baholang — ilovada, buyurtma #{number}.",
        "ru": "MedBron: как прошёл приём? Оцените врача в приложении, заказ #{number}.",
    },
    # Pul yechildi, lekin vaqt band bo'lib qoldi (B3)
    "payment_booking_unavailable": {
        "uz": "MedBron: to'lovingiz qabul qilindi, lekin tanlangan vaqt band bo'lib qoldi. Pul 1–3 kunda qaytariladi.",
        "ru": "MedBron: оплата получена, но выбранное время уже занято. Деньги вернутся в течение 1–3 дней.",
    },
    "payment_reversed": {
        "uz": "MedBron: buyurtma #{number} bo'yicha {amount} so'm to'lov bekor qilindi.",
        "ru": "MedBron: оплата {amount} сум по заказу #{number} отменена.",
    },
    "refund_succeeded": {
        "uz": "MedBron: buyurtma #{number} bo'yicha {amount} so'm qaytarildi. Pul 1–3 kunda kartangizga tushadi.",
        "ru": "MedBron: по заказу #{number} возвращено {amount} сум. Деньги поступят на карту в течение 1–3 дней.",
    },
    "waitlist_slot_available": {
        "uz": "MedBron: {doctor} da {when} vaqt bo'shadi. {minutes} daqiqa ichida bron qiling.",
        "ru": "MedBron: у {doctor} освободилось время {when}. Забронируйте в течение {minutes} минут.",
    },
    "review_rejected": {
        "uz": "MedBron: sharhingiz e'lon qilinmadi. Sabab: {reason}.",
        "ru": "MedBron: ваш отзыв не опубликован. Причина: {reason}.",
    },
    "dispute_resolved": {
        "uz": "MedBron: murojaatingiz ko'rib chiqildi. Qaytariladigan summa: {amount} so'm.",
        "ru": "MedBron: ваше обращение рассмотрено. Сумма возврата: {amount} сум.",
    },
    "clinic_member_added": {
        "uz": "MedBron: siz klinika administratori sifatida qo'shildingiz. Ilovada «Klinika» bo'limi ochildi.",
        "ru": "MedBron: вы добавлены администратором клиники. В приложении появился раздел «Клиника».",
    },
    # --- shifokor ------------------------------------------------------------------------------
    "doctor_onboarding_submitted": {
        "uz": "MedBron: arizangiz qabul qilindi va {due} gacha ko'rib chiqiladi.",
        "ru": "MedBron: заявка принята и будет рассмотрена до {due}.",
    },
    "doctor_approved": {
        "uz": "MedBron: tabriklaymiz! Profilingiz tasdiqlandi — endi bemorlar sizni topa oladi.",
        "ru": "MedBron: поздравляем! Профиль подтверждён — пациенты уже могут вас найти.",
    },
    "doctor_rejected": {
        "uz": "MedBron: arizangiz rad etildi. Sabab: {reason}. Tuzatib, qayta yuborishingiz mumkin.",
        "ru": "MedBron: заявка отклонена. Причина: {reason}. Исправьте и отправьте повторно.",
    },
    "doctor_suspended": {
        "uz": "MedBron: profilingiz vaqtincha to'xtatildi. Sabab: {reason}.",
        "ru": "MedBron: профиль временно приостановлен. Причина: {reason}.",
    },
    "doctor_reinstated": {
        "uz": "MedBron: profilingiz qayta faollashtirildi.",
        "ru": "MedBron: профиль снова активен.",
    },
    "doctor_license_expiring": {
        "uz": "MedBron: litsenziyangiz muddati {date} da tugaydi. Profil to'xtatilmasligi uchun yangilang.",
        "ru": "MedBron: срок лицензии истекает {date}. Обновите, чтобы профиль не был приостановлен.",
    },
    "doctor_clinic_invite": {
        "uz": "MedBron: {clinic} sizni o'z shifokorlari qatoriga taklif qildi. Ilovada qabul qiling yoki rad eting.",
        "ru": "MedBron: {clinic} приглашает вас в команду врачей. Примите или отклоните в приложении.",
    },
    "doctor_affiliation_ended": {
        "uz": "MedBron: klinika bilan hamkorlik yakunlandi. Shu klinikadagi jadvalingiz yopildi.",
        "ru": "MedBron: сотрудничество с клиникой завершено. Расписание в этой клинике закрыто.",
    },
    "doctor_new_booking": {
        "uz": "MedBron: yangi qabul — {when}, #{number}. {place}",
        "ru": "MedBron: новая запись — {when}, #{number}. {place}",
    },
    "doctor_booking_cancelled": {
        "uz": "MedBron: {when} dagi qabul #{number} bekor qilindi.",
        "ru": "MedBron: запись #{number} на {when} отменена.",
    },
    "doctor_booking_rescheduled": {
        "uz": "MedBron: qabul #{number} ko'chirildi — endi {when}.",
        "ru": "MedBron: запись #{number} перенесена — теперь {when}.",
    },
    "doctor_new_review": {
        "uz": "MedBron: qabul #{number} bo'yicha yangi sharh — {rating}/5. Ilovada javob berishingiz mumkin.",
        "ru": "MedBron: новый отзыв по записи #{number} — {rating}/5. Ответить можно в приложении.",
    },
    "doctor_daily_summary": {
        "uz": "MedBron: bugun {count} ta qabul, birinchisi {first}.",
        "ru": "MedBron: сегодня {count} приёмов, первый в {first}.",
    },
    "doctor_payout_calculated": {
        "uz": "MedBron: {period} uchun hisob-kitob tayyor — {amount} so'm.",
        "ru": "MedBron: расчёт за {period} готов — {amount} сум.",
    },
    "doctor_payout_paid": {
        "uz": "MedBron: {amount} so'm to'lov amalga oshirildi.",
        "ru": "MedBron: выплата {amount} сум отправлена.",
    },
    # --- ops (Telegram ops chat; faqat uz) ------------------------------------------------------
    "ops_moderation_new": {"uz": "🩺 Yangi shifokor arizasi: {doctor_id}. SLA: {due}."},
    "ops_confirmation_failed": {
        "uz": "🚨 Pul yechildi, bron yo'q: booking {booking_id}, payment {payment_id}, {amount} so'm ({reason})."
    },
    "ops_refund_failed": {"uz": "🚨 Refund yiqildi: {refund_id} ({amount} so'm), {attempts} urinish: {error}"},
    "ops_discrepancy": {"uz": "⚠️ Reconciliation ({day}, {provider}): {type} — payment {payment_id}"},
    "ops_dispute_opened": {"uz": "📨 Yangi nizo {dispute_id} (bron {booking_id}): {reason}. SLA: {due}."},
    "ops_dispute_sla_breached": {"uz": "⏰ Nizo SLA buzildi: {dispute_id} (muddat {due})."},
}

_cache: dict[tuple[str, str], tuple[float, str | None]] = {}


class TemplateError(ValueError):
    pass


def placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def validate_override(key: str, text: str) -> None:
    """Admin matni standart bilan bir xil parametrlarni ishlatishi shart (yetishmasa — yuborishda xato)."""
    default = DEFAULTS.get(key)
    if default is None:
        raise TemplateError(f"Noma'lum shablon: {key}")
    expected = placeholders(default["uz"])
    try:
        got = placeholders(text)
    except ValueError as exc:
        raise TemplateError(f"Matn formati noto'g'ri: {exc}") from exc
    if got != expected:
        raise TemplateError(f"Parametrlar standart bilan bir xil bo'lishi shart: {sorted(expected)}")


async def _override(session: AsyncSession, key: str, language: str, ttl: int) -> str | None:
    cached = _cache.get((key, language))
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    stmt = select(Template.text).where(Template.key == key, Template.language == language, Template.is_active.is_(True))
    text = (await session.execute(stmt)).scalar_one_or_none()
    _cache[(key, language)] = (now + ttl, text)
    return text


def clear_cache() -> None:
    _cache.clear()


async def render(session: AsyncSession, key: str, language: str, params: dict[str, object], *, ttl: int = 60) -> str:
    lang_map = DEFAULTS.get(key)
    if lang_map is None:
        raise TemplateError(f"Noma'lum shablon: {key}")
    text = (
        await _override(session, key, language, ttl)
        or lang_map.get(language)
        or await _override(session, key, "uz", ttl)
        or lang_map["uz"]
    )
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError) as exc:
        # Shablonda kutilgan parametr kelmagan — deploy xatosi, jim qolmasin
        raise TemplateError(f"'{key}' uchun parametr yetishmayapti: {exc}") from exc


def fmt_when(value: datetime | str) -> str:
    """Mijozga ko'rinadigan vaqt — HAR DOIM Toshkent vaqtida."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return moment.astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")


def fmt_amount(tiyin: int) -> str:
    """Tiyin → so'm, minglar bo'sh joy bilan: 12_500_000 → "125 000"."""
    return f"{tiyin // 100:,}".replace(",", " ")


def place_label(place: str, language: str) -> str:
    if place == "home":
        return "Uy chaqiruvi" if language == "uz" else "Вызов на дом"
    return "Klinikada qabul" if language == "uz" else "Приём в клинике"
