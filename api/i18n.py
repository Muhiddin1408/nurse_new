"""
i18n.py — API javoblari tili (C14).

Til — `Accept-Language` (ilova yuboradi) yoki `?lang=uz|ru`; default o'zbekcha.
`LocaleMiddleware` Django/DRF'ning o'z xabarlarini (validatsiya, 401/403/404)
tarjima qiladi. Bizning biznes xabarlarimiz kodda o'zbekcha yozilgan —
`ApiTranslationMiddleware` javobdagi `detail` ni shu katalog bo'yicha
ruschaga o'giradi. Kalit — o'zbekcha matnning O'ZI (alohida msgid yo'q),
shuning uchun kodda xabar matnini o'zgartirsangiz, katalogni ham yangilang:
`apps/notifications/test_channels.py` katalogda yo'q foydalanuvchi xabarini topadi.

Parametrli xabarlar (`f"... {n} daqiqa ..."`) — `RU_PATTERNS` (regex).
Katalogda yo'q xabar o'zbekcha qoladi (xato emas — faqat tarjima yo'q).
"""

from __future__ import annotations

import re

from django.utils import translation

SUPPORTED = ("uz", "ru")

RU: dict[str, str] = {
    # --- Auth / akkaunt
    "Akkaunt bloklangan": "Аккаунт заблокирован",
    "Faqat O'zbekiston raqamlari (+998) qabul qilinadi": "Принимаются только номера Узбекистана (+998)",
    "Juda ko'p noto'g'ri urinish. Bir soatdan keyin qayta urinib ko'ring":
        "Слишком много неверных попыток. Повторите через час",
    "Juda ko'p urinish": "Слишком много попыток",
    "Juda ko'p urinish. Bir soatdan keyin urinib ko'ring": "Слишком много попыток. Повторите через час",
    "Kod noto'g'ri": "Неверный код",
    "Kod noto'g'ri yoki muddati o'tgan": "Код неверный или истёк",
    "Kod yuborildi": "Код отправлен",
    "Urinishlar soni tugadi. Yangi kod so'rang": "Попытки исчерпаны. Запросите новый код",
    "Token noto'g'ri yoki muddati o'tgan": "Токен неверный или истёк",
    "Bu rol sizga berilmagan": "Эта роль вам не назначена",
    "Avval faol bronlarni bekor qiling yoki qabul yakunlanishini kuting":
        "Сначала отмените активные записи или дождитесь завершения приёма",
    "Shifokor akkaunti qo'llab-quvvatlash xizmati orqali o'chiriladi":
        "Аккаунт врача удаляется через службу поддержки",
    "2FA kodi noto'g'ri yoki eskirgan": "Код 2FA неверный или устарел",
    # --- Bron
    "Avval oldingi bronlarni to'lang yoki bekor qiling": "Сначала оплатите или отмените предыдущие записи",
    "Ba'zi xizmatlar topilmadi yoki faol emas": "Некоторые услуги не найдены или неактивны",
    "Bitta bronda klinika va uy xizmatlarini aralashtirib bo'lmaydi":
        "Нельзя совмещать услуги в клинике и на дому в одной записи",
    "Bron to'lov kutish holatida emas": "Запись не ожидает оплаты",
    "Bronning joriy vaqti topilmadi": "Текущее время записи не найдено",
    "Bu allaqachon shu bronning vaqti": "Это уже время этой записи",
    "Bu bemorda shu vaqtda boshqa qabul bor": "У этого пациента на это время уже есть приём",
    "Bu bron bo'yicha nizo ochib bo'lmaydi": "По этой записи нельзя открыть спор",
    "Bu bron bo'yicha ochiq nizo allaqachon bor": "По этой записи уже есть открытый спор",
    "Bu bron klinikada to'lanadi": "Эта запись оплачивается в клинике",
    "Bu bronni ko'chirib bo'lmaydi": "Эту запись нельзя перенести",
    "Bu vaqt allaqachon band qilingan": "Это время уже занято",
    "Bu vaqt juda yaqin": "Это время слишком близко",
    "Kamida bitta xizmat tanlanishi kerak": "Выберите хотя бы одну услугу",
    "O'tgan sanaga navbatga yozib bo'lmaydi": "Нельзя встать в очередь на прошедшую дату",
    "Qabul hali boshlanmagan": "Приём ещё не начался",
    "Qabul vaqti hali kelmagan": "Время приёма ещё не наступило",
    "Qabulga kam vaqt qoldi — ko'chirib bo'lmaydi": "До приёма осталось мало времени — перенос невозможен",
    "Shifokor bu manzilga bormaydi": "Врач не выезжает по этому адресу",
    "Shifokor hozircha bron qabul qilmaydi": "Врач пока не принимает записи",
    "Tanlangan xizmat bu vaqtdagi qabul joyiga mos emas": "Выбранная услуга не соответствует месту приёма в это время",
    "To'lov muddati o'tgan, vaqtni qaytadan tanlang": "Время на оплату истекло, выберите время заново",
    "Uy chaqiruvi uchun manzil ko'rsatilishi shart": "Для вызова на дом укажите адрес",
    "Xizmatlar takrorlanmasligi kerak": "Услуги не должны повторяться",
    "Yangi vaqt qabul joyiga mos emas": "Новое время не соответствует месту приёма",
    "address_id noto'g'ri": "Неверный address_id",
    "patient_id noto'g'ri": "Неверный patient_id",
    "slot_id noto'g'ri": "Неверный slot_id",
    "Faol bronlari bor — avval ularni bekor qiling": "Есть активные записи — сначала отмените их",
    "Qaytariladigan summa yo'q": "Нет суммы к возврату",
    # --- Promo / sharh
    "Promo kod bu shifokor uchun emas": "Промокод не действует для этого врача",
    "Promo kod faqat birinchi bron uchun": "Промокод только для первой записи",
    "Promo kod limiti tugagan": "Лимит промокода исчерпан",
    "Promo kod muddati tugagan": "Срок действия промокода истёк",
    "Promo kod topilmadi": "Промокод не найден",
    "Bu promo koddan allaqachon foydalangansiz": "Вы уже использовали этот промокод",
    "Baho 1 dan 5 gacha bo'lishi kerak": "Оценка должна быть от 1 до 5",
    "Bu qabulga sharh allaqachon yozilgan": "Отзыв на этот приём уже оставлен",
    "Faqat yakunlangan qabulga sharh yozish mumkin": "Отзыв можно оставить только после завершённого приёма",
    "Sharh yozish muddati o'tgan (30 kun)": "Срок для отзыва истёк (30 дней)",
    "Sharhga faqat bir marta javob berish mumkin": "Ответить на отзыв можно только один раз",
    "Javob bo'sh bo'lmasligi kerak": "Ответ не должен быть пустым",
    "Javobda telefon, havola yoki nomaqbul so'z bo'lmasligi kerak":
        "В ответе не должно быть телефона, ссылок или недопустимых слов",
    "Sharh moderatsiyada emas": "Отзыв не на модерации",
    # --- Katalog / qidiruv
    "lat va lng birga beriladi": "lat и lng передаются вместе",
    "radius noto'g'ri": "Неверный radius",
    "radius: 0 < radius <= 50": "radius: 0 < radius <= 50",
    # C15.4 qidiruv
    "min_rating noto'g'ri": "Неверный min_rating",
    "min_rating: 0 <= min_rating <= 5": "min_rating: 0 <= min_rating <= 5",
    "sort noto'g'ri": "Неверный sort",
    "sort=distance uchun lat va lng kerak": "Для sort=distance нужны lat и lng",
    "place: 'clinic' yoki 'home' bo'lishi kerak": "place: 'clinic' или 'home'",
    # --- Shifokor / jadval
    "Boshlanish vaqti tugashdan oldin bo'lishi kerak": "Время начала должно быть раньше окончания",
    "Bir martada 90 kundan uzun ta'til qo'yib bo'lmaydi": "Нельзя оформить отпуск длиннее 90 дней за раз",
    "Bu kun va vaqtda boshqa ish qoidasi bor": "На этот день и время уже есть другое правило",
    "Bu mutaxassislik profilingizda yo'q": "Этой специальности нет в вашем профиле",
    "Bu oraliqda boshqa ta'til bor": "В этом периоде уже есть отпуск",
    "Faqat bo'sh slotni yopish mumkin — band slotni qabullar orqali bekor qiling":
        "Закрыть можно только свободный слот — занятый отмените через приёмы",
    "Faqat siz qo'lda yopgan kelajak slotni ochish mumkin": "Открыть можно только закрытый вами будущий слот",
    "Faqat tasdiqlangan qabulni boshlash mumkin": "Начать можно только подтверждённый приём",
    "Klinika xizmati uyda bo'lmaydi": "Услуга клиники не оказывается на дому",
    "Mutaxassislik topilmadi": "Специальность не найдена",
    "Narx musbat va chegaradan oshmasligi kerak": "Цена должна быть положительной и в пределах лимита",
    "O'tgan vaqtga ta'til qo'yib bo'lmaydi": "Нельзя оформить отпуск на прошедшее время",
    "Oraliqqa kamida bitta slot sig'ishi kerak": "В интервал должен помещаться хотя бы один слот",
    "Profilni faqat qoralama yoki rad etilgan holatda tahrirlash mumkin":
        "Профиль можно редактировать только в статусе черновика или отклонённого",
    "Qoida topilmadi": "Правило не найдено",
    "Rad etish sababi majburiy": "Укажите причину отклонения",
    "Bekor qilish sababi majburiy": "Укажите причину отмены",
    "To'xtatish sababi majburiy": "Укажите причину приостановки",
    "Siz bu klinikada faol ishlamaysiz": "Вы не работаете в этой клинике",
    "Slot topilmadi": "Слот не найден",
    # C12 paketlar
    "Paket topilmadi yoki faol emas": "Пакет не найден или неактивен",
    "Bu xizmat bo'yicha faol paketingiz allaqachon bor": "У вас уже есть активный пакет по этой услуге",
    "Ta'til topilmadi": "Отпуск не найден",
    "Tugash vaqti boshlanishdan keyin bo'lishi kerak": "Время окончания должно быть позже начала",
    "Uy chaqiruvi qoidasi uchun profilda uy chaqiruvini yoqing":
        "Для правила вызова на дом включите вызовы на дом в профиле",
    "Uy xizmati uchun profilda uy chaqiruvini yoqing": "Для услуги на дому включите вызовы на дом в профиле",
    "Litsenziya muddati o'tgan — avval yangilang": "Срок лицензии истёк — сначала обновите её",
    "Litsenziya muddati o'tgan — tasdiqlab bo'lmaydi": "Срок лицензии истёк — одобрить нельзя",
    "Shifokorning to'lov rekvizitlari kiritilmagan": "Платёжные реквизиты врача не указаны",
    "Bank to'lov hujjati raqami majburiy": "Номер банковского платёжного документа обязателен",
    "Faqat PDF, JPEG yoki PNG fayl qabul qilinadi": "Принимаются только файлы PDF, JPEG или PNG",
    "Fayl bo'sh": "Файл пустой",
    "Havola yaroqsiz yoki muddati o'tgan": "Ссылка недействительна или устарела",
    "Hujjat topilmadi": "Документ не найден",
    "Hujjat turi noto'g'ri": "Неверный тип документа",
    "Oraliq noto'g'ri (max 1 yil)": "Неверный период (максимум 1 год)",
    "from va to: YYYY-MM-DD": "from и to: YYYY-MM-DD",
    "from/to: YYYY-MM-DD": "from/to: YYYY-MM-DD",
    "period: today|week|month yoki from/to": "period: today|week|month или from/to",
    "date_to date_from dan oldin": "date_to раньше date_from",
    "date_to date_from dan oldin bo'lishi mumkin emas": "date_to не может быть раньше date_from",
    "date_to, date_from dan oldin bo'lmasligi kerak": "date_to не должен быть раньше date_from",
    "valid_to valid_from dan oldin bo'lmasligi kerak": "valid_to не должен быть раньше valid_from",
    # --- Klinika admini
    "Bu kun allaqachon dam olish kuni": "Этот день уже выходной",
    "Bu nomli xona allaqachon bor": "Кабинет с таким названием уже есть",
    "Bu raqamda shifokor profili yo'q — shifokor avval ilovada ro'yxatdan o'tsin":
        "На этот номер нет профиля врача — врач должен сначала зарегистрироваться в приложении",
    "Dam olish kunini faqat kelajak uchun qo'yish mumkin": "Выходной можно назначить только на будущее",
    "Oraliq: date_from <= date_to, ko'pi bilan 1 yil": "Период: date_from <= date_to, не более 1 года",
    "Shifokor allaqachon klinikada": "Врач уже в клинике",
    "Taklif allaqachon yuborilgan": "Приглашение уже отправлено",
    "clinic_id majburiy — siz bir nechta klinikani boshqarasiz": "clinic_id обязателен — вы управляете несколькими клиниками",
    "action: pause | resume | end": "action: pause | resume | end",
    'Davomiylik 5–480 daqiqa': "Длительность 5–480 минут",
    'weekday 0 (dushanba) … 6 (yakshanba)': "weekday: 0 (понедельник) … 6 (воскресенье)",
    "working_hours kaliti: '0' (dushanba) … '6' (yakshanba)": "Ключ working_hours: '0' (понедельник) … '6' (воскресенье)",
    'working_hours: ["08:00", "18:00"] yoki null': 'working_hours: ["08:00", "18:00"] или null',
    "clinic_id noto'g'ri": "Неверный clinic_id",
    "service_id noto'g'ri": "Неверный service_id",
    "working_hours: obyekt bo'lishi kerak": "working_hours: должен быть объектом",
    "working_hours: ochilish yopilishdan oldin bo'lishi kerak": "working_hours: открытие должно быть раньше закрытия",
    # --- Nizolar
    "Shifokor foydasiga hal qilinganda refund bo'lmaydi": "При решении в пользу врача возврат не производится",
    "in_favor_of: client yoki doctor": "in_favor_of: client или doctor",
    "absent: client yoki doctor bo'lishi kerak": "absent: client или doctor",
}

RU_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(\d+) soniyadan keyin qayta urinib ko'ring$"), r"Повторите через \1 сек."),
    (re.compile(r"^Bronni ko'pi bilan (\d+) marta ko'chirish mumkin\. Yangi bron qiling\.$"),
     r"Запись можно перенести не более \1 раз. Создайте новую запись."),
    (re.compile(r"^Bu xizmatlar uchun (\d+) daqiqa kerak, tanlangan vaqt (\d+) daqiqa$"),
     r"Для этих услуг нужно \1 мин., выбранное время — \2 мин."),
    (re.compile(r"^Bu maydonlar moderatsiyasiz o'zgarmaydi: (.+)$"), r"Эти поля нельзя изменить без модерации: \1"),
    (re.compile(r"^Holat (\S+) dan (\S+) ga o'tib bo'lmaydi$"), r"Нельзя перейти из статуса \1 в \2"),
    (re.compile(r"^Ko'pi bilan (\d+) ta sevimli$"), r"Не более \1 избранных"),
    (re.compile(r"^Noma'lum status: (.+)$"), r"Неизвестный статус: \1"),
    (re.compile(r"^Oraliq (\d+) kundan oshmasligi kerak$"), r"Период не должен превышать \1 дн."),
    (re.compile(r"^Fayl (\d+) MB dan katta$"), r"Файл больше \1 МБ"),
    (re.compile(r"^Promo kod ([\d ]+) so'mdan boshlab ishlaydi$"), r"Промокод действует от \1 сум"),
    (re.compile(r"^(.+): bemor kamida (\d+) yoshda bo'lishi kerak$"), r"\1: пациенту должно быть не меньше \2 лет"),
    (re.compile(r"^(.+): bemor ko'pi bilan (\d+) yoshda bo'lishi kerak$"), r"\1: пациенту должно быть не больше \2 лет"),
    (re.compile(r"^(.+): bu mutaxassislik bemor jinsiga mos emas$"), r"\1: специальность не подходит по полу пациента"),
    (re.compile(r"^(.+): bu shifokor bolalarni qabul qilmaydi$"), r"\1: врач не принимает детей"),
    (re.compile(r"^(.+): bu shifokor faqat bolalar bilan ishlaydi$"), r"\1: врач работает только с детьми"),
    (re.compile(r"^(\w+) majburiy \(YYYY-MM-DD\)$"), r"\1 обязателен (YYYY-MM-DD)"),
    (re.compile(r"^(\w+) noto'g'ri$"), r"Неверный \1"),
]


def translate(text: str, language: str | None = None) -> str:
    lang = language or translation.get_language() or "uz"
    if not lang.startswith("ru") or not isinstance(text, str):
        return text
    if text in RU:
        return RU[text]
    for pattern, repl in RU_PATTERNS:
        if pattern.match(text):
            return pattern.sub(repl, text)
    return text


def request_language(request) -> str:
    """`?lang=` > Accept-Language (LocaleMiddleware allaqachon faollashtirgan)."""
    lang = (request.GET.get("lang") or translation.get_language() or "uz")[:2]
    return lang if lang in SUPPORTED else "uz"


class ApiTranslationMiddleware:
    """`LocaleMiddleware` DAN KEYIN. Faqat DRF javoblari (`response.data`) —
    `detail` va maydon xatolari ro'yxatidagi bizning xabarlarimiz tarjima qilinadi."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = request.GET.get("lang")
        if lang in SUPPORTED:
            translation.activate(lang)
            request.LANGUAGE_CODE = lang
        response = self.get_response(request)
        data = getattr(response, "data", None)
        if request_language(request) == "ru" and isinstance(data, dict) and response.status_code >= 400:
            new = {k: _translate_value(v) for k, v in data.items()}
            if new != data:
                from rest_framework.renderers import JSONRenderer

                response.data = new
                response.content = JSONRenderer().render(new)
        response.headers.setdefault("Content-Language", request_language(request))
        return response


def _translate_value(value):
    if isinstance(value, str):
        return translate(value, "ru")
    if isinstance(value, list):
        return [translate(v, "ru") if isinstance(v, str) else v for v in value]
    return value


def localized(obj, field: str, language: str | None = None) -> str:
    """Katalog matni: `name_ru` bo'lsa va til ruscha bo'lsa — u, aks holda `name`."""
    lang = (language or translation.get_language() or "uz")[:2]
    if lang == "ru":
        value = getattr(obj, f"{field}_ru", "") or ""
        if value:
            return value
    return getattr(obj, field)
