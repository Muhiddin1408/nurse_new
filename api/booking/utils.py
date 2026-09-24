from datetime import datetime

from rest_framework import serializers, status
from rest_framework.response import Response

import logging

from api.booking.services import BookingError, InvalidBookingRequest, SlotTaken
from api.observability.correlation import get_correlation_id

logger = logging.getLogger(__name__)


def _alternatives(exc: SlotTaken) -> list[dict]:
    """Muqobil vaqtlarni topish XATO JAVOBINI buzmasligi kerak.

    Bu qo'shimcha qulaylik: qidiruv yiqilsa ham mijoz asosiy javobni
    (409 "band") olishi shart."""
    from api.booking import waitlist

    if exc.start_at is None:
        return []
    try:
        return waitlist.find_alternatives(
            doctor_id=exc.doctor_id, start_at=exc.start_at, place=exc.place
        )
    except Exception:  # noqa: BLE001
        logger.exception("Muqobil vaqtlarni topib bo'lmadi")
        return []


def custom_exception_handler(exc, context):
    """settings.py: REST_FRAMEWORK = {"EXCEPTION_HANDLER": "booking.api.custom_exception_handler"}

    Xato kodlarini BITTA joyda belgilaymiz. Har bir view ichida try/except
    yozsangiz, ular vaqt o'tib bir-biridan farq qila boshlaydi va mobil
    ilova chalkashadi.

    Kod tanlovi muhim:
        400 — so'rov noto'g'ri, qayta yuborishning ma'nosi yo'q
        409 — so'rov to'g'ri, lekin hozir bajarib bo'lmaydi (slot band).
              Klient boshqa vaqt tanlashi kerak. Bu 400 EMAS.
    """
    from rest_framework.views import exception_handler

    if isinstance(exc, InvalidBookingRequest):
        response = Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    elif isinstance(exc, SlotTaken):
        # C10: "band" — oqimning tugashi emas. Mijozga darhol muqobil
        # vaqtlarni va navbatga yozilish imkonini beramiz.
        response = Response(
            {
                "detail": str(exc),
                "code": "slot_taken",
                "alternatives": _alternatives(exc),
                "waitlist_available": True,
            },
            status=status.HTTP_409_CONFLICT,
        )
    elif isinstance(exc, BookingError):
        response = Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    else:
        response = exception_handler(exc, context)

    # E13: mijoz support'ga aytishi uchun — shu ID bo'yicha butun zanjir loglari topiladi
    if response is not None and isinstance(response.data, dict):
        response.data["trace_id"] = get_correlation_id()
    return response


# ---------------------------------------------------------------------------
# Yordamchi
# ---------------------------------------------------------------------------


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    parsed = serializers.DateTimeField().to_internal_value(raw)
    return parsed
