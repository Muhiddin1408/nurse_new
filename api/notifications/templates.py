TEMPLATES: dict[str, dict[str, str]] = {
    "booking_confirmed": {
        "uz": "MedBron: buyurtmangiz #{number} tasdiqlandi. Vaqtida kuting.",
        "ru": "MedBron: ваш заказ #{number} подтверждён.",
    },
    "booking_cancelled": {
        "uz": "MedBron: buyurtma #{number} bekor qilindi.",
        "ru": "MedBron: заказ #{number} отменён.",
    },
    # ⬇ `booking_cancelled` dan ATAYLAB boshqa matn. Mijoz "bekor qilindi"
    # o'qisa, buni O'ZI qilgan deb o'ylaydi va qo'llab-quvvatlashga yozadi.
    # Aslida sabab boshqa — to'lov ulgurmadi — va yechim ham boshqa:
    # qayta bron qilish kerak. SMS aynan shuni aytishi kerak.
    "booking_expired": {
        "uz": "MedBron: buyurtma #{number} to'lov qilinmagani uchun bekor bo'ldi. Qayta bron qilishingiz mumkin.",
        "ru": "MedBron: заказ #{number} отменён — оплата не поступила вовремя. Вы можете забронировать заново.",
    },
    "refund_succeeded": {
        "uz": "MedBron: buyurtma #{number} bo'yicha {amount} so'm qaytarildi. Pul 1–3 kunda kartangizga tushadi.",
        "ru": "MedBron: по заказу #{number} возвращено {amount} сум. Деньги поступят на карту в течение 1–3 дней.",
    },
    # B3 fail-safe: pul yechildi, lekin vaqt band bo'lib qoldi
    "payment_booking_unavailable": {
        "uz": "MedBron: to'lovingiz qabul qilindi, lekin tanlangan vaqt band bo'lib qoldi. Pul 1–3 kunda qaytariladi.",
        "ru": "MedBron: оплата получена, но выбранное время уже занято. Деньги вернутся в течение 1–3 дней.",
    },
    # --- C9: rejalashtirilgan eslatmalar ---------------------------------
    # T-24: mijoz kunini rejalashtirishi uchun — bekor qilsa slot hali sotiladi
    "booking_reminder_24h": {
        "uz": "MedBron: ertaga {when} da qabul. {place}. Bekor qilish yoki ko'chirish — ilovada.",
        "ru": "MedBron: завтра в {when} приём. {place}. Отменить или перенести — в приложении.",
    },
    # T-2: kelish uchun oxirgi turtki — no-show shu yerda kamayadi
    "booking_reminder_2h": {
        "uz": "MedBron: 2 soatdan keyin qabul — {when}. {place}. Kechiksangiz oldindan xabar bering.",
        "ru": "MedBron: приём через 2 часа — {when}. {place}. Предупредите, если опаздываете.",
    },
    # C3: ko'chirildi — mijoz YANGI vaqtni ko'rishi shart, aks holda u
    # eski vaqtga keladi
    "booking_rescheduled": {
        "uz": "MedBron: buyurtma #{number} yangi vaqtga ko'chirildi — {when}.",
        "ru": "MedBron: заказ #{number} перенесён на {when}.",
    },
    "doctor_booking_rescheduled": {
        "uz": "MedBron: qabul #{number} ko'chirildi — endi {when}.",
        "ru": "MedBron: запись #{number} перенесена — теперь {when}.",
    },
    # C10: navbatdagi odamga bo'shagan vaqt haqida
    "waitlist_slot_available": {
        "uz": "MedBron: {doctor} da {when} vaqt bo'shadi. 15 daqiqa ichida bron qiling.",
        "ru": "MedBron: у {doctor} освободилось время {when}. Забронируйте в течение 15 минут.",
    },
    # T+2: sharh so'rovi (C11 oqimiga kirish nuqtasi)
    "booking_review_request": {
        "uz": "MedBron: qabul qanday o'tdi? Shifokorni baholang — ilovada, buyurtma #{number}.",
        "ru": "MedBron: как прошёл приём? Оцените врача в приложении, заказ #{number}.",
    },
    # --- D9: shifokorga ---------------------------------------------------
    "doctor_clinic_invite": {
        "uz": "MedBron: {clinic} sizni o'z shifokorlari qatoriga taklif qildi. Ilovada qabul qiling yoki rad eting.",
        "ru": "MedBron: {clinic} приглашает вас в команду врачей. Примите или отклоните в приложении.",
    },
    "doctor_new_review": {
        "uz": "MedBron: qabul #{number} bo'yicha yangi sharh — {rating}/5. Ilovada javob berishingiz mumkin.",
        "ru": "MedBron: новый отзыв по записи #{number} — {rating}/5. Ответить можно в приложении.",
    },
    "doctor_new_booking": {
        "uz": "MedBron: yangi qabul — {when}, #{number}. {place}",
        "ru": "MedBron: новая запись — {when}, #{number}. {place}",
    },
    "doctor_booking_cancelled": {
        "uz": "MedBron: {when} dagi qabul #{number} bekor qilindi.",
        "ru": "MedBron: запись #{number} на {when} отменена.",
    },
    "doctor_daily_summary": {
        "uz": "MedBron: bugun {count} ta qabul, birinchisi {first}.",
        "ru": "MedBron: сегодня {count} приёмов, первый в {first}.",
    },
    "doctor_payout_paid": {
        "uz": "MedBron: {period} uchun {amount} so'm to'lov amalga oshirildi.",
        "ru": "MedBron: выплата {amount} сум за {period} отправлена.",
    },
    "doctor_license_expiring": {
        "uz": "MedBron: litsenziyangiz muddati {date} da tugaydi. Profilni to'xtatilmasligi uchun yangilang.",
        "ru": "MedBron: срок лицензии истекает {date}. Обновите, чтобы профиль не был приостановлен.",
    },
    "otp": {
        "uz": "MedBron tasdiqlash kodi: {code}. Hech kimga aytmang.",
        "ru": "Код подтверждения MedBron: {code}. Никому не сообщайте.",
    },
}


TEMPLATE_CACHE_SECONDS = 60


def placeholders(text: str) -> set[str]:
    import string

    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _db_text(template: str, language: str) -> str | None:
    """C13: bazadagi matn (admin o'zgartiradi). Kesh 60 s — har SMS uchun SELECT emas."""
    from django.core.cache import cache

    key = f"ntpl:{template}:{language}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    from apps.notifications.models import NotificationTemplate

    text = (NotificationTemplate.objects.filter(key=template, language=language, is_active=True)
            .values_list("text", flat=True).first())
    cache.set(key, text or "", TEMPLATE_CACHE_SECONDS)
    return text


def render_template(template: str, language: str, params: dict[str, str]) -> str:
    lang_map = TEMPLATES.get(template)
    if lang_map is None:
        raise ValueError(f"Noma'lum shablon: {template}")

    text = (_db_text(template, language) or lang_map.get(language)
            or _db_text(template, "uz") or lang_map.get("uz"))
    try:
        return text.format(**params)
    except KeyError as exc:
        # Shablonda kutilgan parametr kelmagan — bu deploy vaqtidagi xato,
        # jim qolib ketmasligi kerak
        raise ValueError(f"Shablon '{template}' uchun parametr yetishmayapti: {exc}") from exc

