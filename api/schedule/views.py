"""
schedule/views.py — Faza 1 (kesh: Faza 6)

Jadval o'qish endpointlari.

NEGA BU YERDA, `booking` DA EMAS:
    Slot — jadval modulining tushunchasi. Bron uni faqat ISHLATADI, unga
    egalik qilmaydi. Ilgari `DoctorSlotsView` `booking/views.py` da turardi
    va o'sha fayl faqat shu view uchun `schedule_services` ni import qilardi —
    ya'ni modul chegarasi teskari tomonga qaragan edi. Faza 4 da jadval
    alohida servisga chiqqanda, bu view u bilan birga ko'chishi kerak.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api.schedule import cache as schedule_cache
from api.schedule import services as schedule_services
from api.schedule.serializers import SlotSerializer
from api.schedule.utils import parse_date, parse_dt
from api.docs import DETAIL, TAG_SCHEDULE


@extend_schema(
    tags=[TAG_SCHEDULE],
    summary="Shifokorning bo'sh vaqtlari",
    description=(
        "Ikki rejim: `date` (yoki parametrsiz = bugun) — keshlanadi; "
        "`date_from`/`date_to` — oraliq, max 30 kun, max 200 slot. Vaqtlar UTC."
    ),
    parameters=[
        OpenApiParameter("date", OpenApiTypes.DATE, description="Bitta kun (Toshkent vaqti)"),
        OpenApiParameter("date_from", OpenApiTypes.DATETIME),
        OpenApiParameter("date_to", OpenApiTypes.DATETIME),
    ],
    responses={200: SlotSerializer(many=True), 400: DETAIL},
    auth=[],
)
class DoctorSlotsView(APIView):
    """GET /api/v1/schedule/doctors/{doctor_id}/slots

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  ENG KO'P CHAQIRILADIGAN ENDPOINT.                                   ║
    ║  Dizayndagi har bir shifokor kartochkasi ostida bo'sh vaqtlar bor —  ║
    ║  ya'ni ro'yxat ochilganda 20 ta shifokor uchun 20 marta chaqiriladi. ║
    ║                                                                       ║
    ║  Kelajak uchun eslatma: ro'yxat ekranida N marta chaqirmaslik uchun  ║
    ║  batch endpoint kerak bo'ladi (`?doctor_ids=a,b,c`). Buni yuk        ║
    ║  testidan KEYIN qo'shing — hozir erta optimallashtirish bo'ladi.     ║
    ╚══════════════════════════════════════════════════════════════════════╝

    IKKI REJIM — va faqat bittasi keshlanadi:

        ?date=2026-07-30   (yoki parametrsiz -> bugun)
            BITTA KUN. Keshdan o'qiladi (`get_available_slots_cached`).
            Bu asosiy, "issiq" yo'l: shifokor kartochkasi aynan shuni
            so'raydi va yuk ham shu yerga tushadi.

        ?date_from=...&date_to=...
            ORALIQ. Keshlanmaydi, to'g'ridan-to'g'ri bazadan.

    NEGA ORALIQ KESHLANMAYDI: kesh kaliti kun bo'yicha qurilgan
    (`slots:v1:{doctor}:{kun}`). Ixtiyoriy oraliqni keshlash uchun kalitga
    ikkala chegarani qo'shish kerak bo'lardi — u holda har bir noyob oraliq
    o'z yozuvini yaratadi, hit darajasi nolga intiladi va kesh faqat xotira
    yeydi. Kalit kam xil bo'lishi kerak. Oraliq so'rovi kamdan-kam
    chaqiriladi (kalendar ko'rinishi), shuning uchun bu to'g'ri almashuv.

    PARAMETRSIZ CHAQIRUV BUGUNNI BERADI: bu ataylab — shifokor kartochkasi
    "eng yaqin bo'sh vaqt" ni so'raydi, va shu tufayli eng ko'p keladigan
    so'rov avtomatik ravishda keshlanadigan yo'lga tushadi.
    """

    permission_classes = []  # ochiq — ro'yxatdan o'tmagan mijoz ham ko'radi

    MAX_RANGE_DAYS = 30
    MAX_SLOTS = 200

    def get(self, request: Request, doctor_id):
        date_from = parse_dt(request.query_params.get("date_from"))
        date_to = parse_dt(request.query_params.get("date_to"))

        if date_from or date_to:
            return self._range_response(doctor_id, date_from, date_to)

        day = parse_date(request.query_params.get("date")) or schedule_services.today_local()
        return self._single_day_response(doctor_id, day)

    # -- bitta kun: keshlanadi -------------------------------------------

    def _single_day_response(self, doctor_id, day) -> Response:
        # ⬇ Kesh allaqachon `{"id": ..., "start_at": ..., "end_at": ...}`
        # ko'rinishidagi dict qaytaradi — SlotSerializer bilan aynan bir xil
        # shakl. Qayta serializatsiya qilmaymiz: keshning butun ma'nosi
        # ishni O'TKAZIB YUBORISHDA, uni takrorlashda emas.
        data = schedule_cache.get_available_slots_cached(doctor_id, day)

        response = Response(data)
        # Kesh TTL bilan bir xil — brauzer/CDN ham shuncha ushlab tursin.
        # `public`, chunki javob foydalanuvchiga bog'liq emas (endpoint ochiq).
        response["Cache-Control"] = f"public, max-age={schedule_cache.CACHE_TTL_SECONDS}"
        return response

    # -- oraliq: keshlanmaydi --------------------------------------------

    def _range_response(self, doctor_id, date_from, date_to) -> Response:
        if date_from and date_to:
            if date_to < date_from:
                return Response(
                    {"detail": "date_to, date_from dan oldin bo'lmasligi kerak"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if (date_to - date_from).days > self.MAX_RANGE_DAYS:
                # ⬇ Cheklovsiz oraliq — DoS vektori: kimdir 10 yillik
                # so'rov yuborsa, baza butun jadvalni skanerlaydi.
                return Response(
                    {"detail": f"Oraliq {self.MAX_RANGE_DAYS} kundan oshmasligi kerak"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        slots = schedule_services.get_available_slots(
            doctor_id=doctor_id, date_from=date_from, date_to=date_to
        )[: self.MAX_SLOTS]

        return Response(SlotSerializer(slots, many=True).data)