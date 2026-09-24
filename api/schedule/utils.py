from datetime import date, datetime

from rest_framework import serializers


def parse_dt(raw: str | None) -> datetime | None:
    """ISO-8601 datetime -> `datetime`, bo'sh bo'lsa `None`.

    DRF ning `DateTimeField` idan foydalanamiz, `datetime.fromisoformat` dan
    emas: DRF `USE_TZ` ni hisobga oladi va noto'g'ri formatda tushunarli
    `ValidationError` ko'taradi (u 400 ga aylanadi), `ValueError` emas —
    u 500 bo'lib ketardi.

    ⚠️ Bu funksiya `booking/utils.py:_parse_dt` ning nusxasi. ATAYLAB
    nusxalangan: `schedule` moduli `booking` dan hech narsa import qilmaydi
    (modul chegarasi qoidasi — fayl boshidagi `services.py` izohiga qarang).
    Faza 4 da jadval alohida servisga chiqqanda, u yerda `booking` degan
    modul umuman bo'lmaydi. Uch qatorlik nusxa — bog'liqlikdan arzon narx.
    """
    if not raw:
        return None
    return serializers.DateTimeField().to_internal_value(raw)


def parse_date(raw: str | None) -> date | None:
    """`YYYY-MM-DD` -> `date`, bo'sh bo'lsa `None`."""
    if not raw:
        return None
    return serializers.DateField().to_internal_value(raw)