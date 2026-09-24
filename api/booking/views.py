import hashlib
import json

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from api.booking.serializers import (
    BookingSerializer,
    CancelBookingSerializer,
    CancellationPreviewSerializer,
    CompleteBookingSerializer,
    CreateBookingSerializer,
    DisputeSerializer,
    NoShowSerializer,
    OpenDisputeSerializer,
    RescheduleBookingSerializer,
    ResolveDisputeSerializer,
    WaitlistEntrySerializer,
    WaitlistJoinSerializer,
)
from api.booking import waitlist as waitlist_services
from api.catalog import services as catalog_services
from api.booking import disputes
from api.permissions import IsPlatformAdmin
from apps.booking.models import Dispute
from api.docs import DETAIL, TAG_BOOKING
from . import services as booking_services
from apps.booking.models import Booking
from api.booking.services import BookingRequest


def _payload_hash(data: dict) -> str:
    """So'rov tanasining barqaror xeshi (kalitlar tartibi va UUID turi ta'sir qilmaydi)."""
    canonical = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# `DoctorSlotsView` bu yerdan `api/schedule/views.py` ga ko'chirildi.
# Sabab: slot — jadval modulining tushunchasi, bron unga egalik qilmaydi.
# Yangi manzil: GET /api/v1/schedule/doctors/{id}/slots (u yerda keshlanadi).


class MyBookingsPagination(CursorPagination):
    """C15.1: cursor — offset emas. Yangi bron qo'shilganda sahifalar
    "siljimaydi" (offset'da mijoz bitta bronni ikki marta ko'rardi) va
    chuqur sahifa ham indeks (client, -created_at) bo'yicha tez."""

    ordering = ("-created_at", "-id")
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 50


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Mening bronlarim (cursor paginatsiya)",
    parameters=[
        OpenApiParameter("status", OpenApiTypes.STR, description="Vergul bilan: confirmed,completed"),
        OpenApiParameter("date_from", OpenApiTypes.DATE, description="Qabul sanasi (Toshkent), shu kundan"),
        OpenApiParameter("date_to", OpenApiTypes.DATE, description="Qabul sanasi, shu kungacha (ichida)"),
        OpenApiParameter("cursor", OpenApiTypes.STR),
        OpenApiParameter("page_size", OpenApiTypes.INT, description="Default 20, max 50"),
    ],
    responses={200: BookingSerializer(many=True), 400: DETAIL},
)
class MyBookingsView(APIView):
    """GET /api/v1/booking/bookings/mine?status=&date_from=&date_to=&cursor= — "Moi zakazy" ekrani.

    Javob: `{"next": url|null, "previous": url|null, "results": [...]}`."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request):
        qs = Booking.objects.filter(client=request.user).prefetch_related("items")  # N+1 yo'q
        try:
            qs = _filter_my_bookings(qs, request.query_params)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        paginator = MyBookingsPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(BookingSerializer(page, many=True).data)


def _filter_my_bookings(qs, params):
    from datetime import date, datetime, time

    from api.schedule.services import LOCAL_TZ

    if params.get("status"):
        statuses = [s.strip() for s in params["status"].split(",") if s.strip()]
        unknown = set(statuses) - set(Booking.Status.values)
        if unknown:
            raise ValueError(f"Noma'lum status: {', '.join(sorted(unknown))}")
        qs = qs.filter(status__in=statuses)
    for name, lookup, bound in (("date_from", "slot__start_at__gte", time.min),
                                ("date_to", "slot__start_at__lte", time.max)):
        if params.get(name):
            try:
                day = date.fromisoformat(params[name])
            except ValueError:
                raise ValueError(f"{name}: YYYY-MM-DD") from None
            # "Kun" — Toshkent kuni (C8 qoidasi), UTC emas
            qs = qs.filter(**{lookup: datetime.combine(day, bound, tzinfo=LOCAL_TZ)})
    return qs


@extend_schema(tags=[TAG_BOOKING], summary="Bron tafsiloti", responses={200: BookingSerializer, 404: None})
class BookingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request, booking_id):
        booking = (
            Booking.objects.filter(id=booking_id, client=request.user)
            .prefetch_related("items")
            .first()
        )
        if booking is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


# ---------------------------------------------------------------------------
# Yozish endpointlari
# ---------------------------------------------------------------------------


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Bron yaratish",
    description=(
        "Slot 10 daqiqaga HELD bo'ladi va bron `pending_payment` holatida yaratiladi. "
        "Uy chaqiruvi xizmatida `address_id` majburiy; klinika va uy xizmatlarini bitta bronda aralashtirib bo'lmaydi."
    ),
    parameters=[
        OpenApiParameter(
            "Idempotency-Key", OpenApiTypes.STR, OpenApiParameter.HEADER,
            description="Har bron urinishi uchun bitta UUID. Takroriy so'rov o'sha bronni 200 bilan qaytaradi.",
        )
    ],
    request=CreateBookingSerializer,
    responses={201: BookingSerializer, 200: BookingSerializer, 400: DETAIL, 409: DETAIL},
)
class CreateBookingView(APIView):
    """POST /api/v1/bookings — "Vizvat" tugmasi.

    IDEMPOTENCY-KEY:
        Mobil ilova tugmani ikki marta bosishi, tarmoq javobni yo'qotib
        so'rovni takrorlashi mutlaqo normal hol. Kalitsiz bunday holatda
        ikkita bron yaratiladi va mijozdan ikki marta pul yechiladi.

        Klient har bir bron urinishi uchun bitta UUID generatsiya qiladi va
        `Idempotency-Key` headerida yuboradi. Takroriy so'rov kelsa,
        yangi bron yaratmaymiz — o'shanisini qaytaramiz.

        Buning uchun Booking modeliga qo'shing:
            idempotency_key = models.CharField(
                max_length=100, unique=True, null=True, blank=True
            )

        Faza 4 da bu tushuncha to'lovga ham tarqaladi va o'sha yerda
        hayotiy ahamiyat kasb etadi.
    """

    permission_classes = [IsAuthenticated]
    write_throttle_scope = "booking_create"  # A7

    def post(self, request: Request):
        serializer = CreateBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        idem_key = request.headers.get("Idempotency-Key")
        request_hash = _payload_hash(data)
        if idem_key is not None:
            if not 1 <= len(idem_key) <= 64:
                return Response(
                    {"detail": "Idempotency-Key uzunligi 1–64 belgi bo'lishi kerak"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Kalit FOYDALANUVCHI doirasida qidiriladi (A5)
            existing = Booking.objects.filter(
                idempotency_key=idem_key, client=request.user
            ).first()
            if existing:
                if existing.request_hash and existing.request_hash != request_hash:
                    return Response(
                        {"detail": "Idempotency key reused with different payload"},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )
                # Takroriy so'rov — 200 qaytaramiz, 201 emas
                return Response(BookingSerializer(existing).data)

        booking_request = BookingRequest(
            client_id=request.user.id,
            patient_id=data["patient_id"],
            doctor_id=data["doctor_id"],
            slot_id=data["slot_id"],
            service_ids=data["service_ids"],
            address_id=data.get("address_id"),
            comment=data.get("comment", ""),
            promo_code=data.get("promo_code", ""),
        )

        booking = booking_services.create_booking(booking_request)

        if idem_key:
            Booking.objects.filter(id=booking.id).update(
                idempotency_key=idem_key, request_hash=request_hash
            )

        return Response(
            BookingSerializer(booking).data, status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Qabulni yakunlash (admin, vaqtinchalik — D4 gacha)",
    request=CompleteBookingSerializer,
    responses={200: BookingSerializer, 403: None, 404: None, 409: DETAIL},
)
class AdminCompleteBookingView(APIView):
    """POST /api/v1/booking/bookings/{id}/complete"""

    permission_classes = [IsPlatformAdmin]

    def post(self, request: Request, booking_id):
        serializer = CompleteBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not Booking.objects.filter(id=booking_id).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        booking = booking_services.complete_booking(
            booking_id, by=Booking.Actor.ADMIN, note=serializer.validated_data.get("note", "")
        )
        return Response(BookingSerializer(booking).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Qabul bo'lmadi — no-show (admin, vaqtinchalik — D4 gacha)",
    request=NoShowSerializer,
    responses={200: BookingSerializer, 403: None, 404: None, 409: DETAIL},
)
class AdminNoShowBookingView(APIView):
    """POST /api/v1/booking/bookings/{id}/no-show"""

    permission_classes = [IsPlatformAdmin]

    def post(self, request: Request, booking_id):
        serializer = NoShowSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not Booking.objects.filter(id=booking_id).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        booking = booking_services.mark_no_show(
            booking_id,
            absent=serializer.validated_data["absent"],
            reported_by=Booking.Actor.ADMIN,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(BookingSerializer(booking).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Bronni bekor qilish",
    request=CancelBookingSerializer,
    responses={200: BookingSerializer, 404: None, 409: DETAIL},
)
class CancelBookingView(APIView):
    """POST /api/v1/bookings/{id}/cancel"""

    permission_classes = [IsAuthenticated]
    write_throttle_scope = "booking_cancel"  # A7

    def post(self, request: Request, booking_id):
        serializer = CancelBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not Booking.objects.filter(id=booking_id, client=request.user).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)

        booking = booking_services.cancel_booking(
            booking_id, reason=serializer.validated_data.get("reason", ""), actor="client"
        )
        return Response(BookingSerializer(booking).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Navbatga yozilish / navbatim",
    request=WaitlistJoinSerializer,
    responses={200: WaitlistEntrySerializer(many=True), 201: WaitlistEntrySerializer, 400: DETAIL},
)
class WaitlistView(APIView):
    """GET/POST /api/v1/booking/waitlist

    Slot band bo'lganda mijoz ketib qolmasin: u navbatga yoziladi va
    vaqt bo'shaganda birinchi bo'lib xabar oladi.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request):
        entries = waitlist_services.list_mine(request.user.id)
        return Response(WaitlistEntrySerializer(entries, many=True).data)

    def post(self, request: Request):
        serializer = WaitlistJoinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # A4: tasdiqlanmagan shifokor — xuddi mavjud emasdek
        if not catalog_services.is_doctor_bookable(data["doctor_id"]):
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            entry = waitlist_services.join(client_id=request.user.id, **data)
        except waitlist_services.WaitlistError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            WaitlistEntrySerializer(entry).data, status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=[TAG_BOOKING], summary="Navbatdan chiqish", responses={204: None, 404: None}
)
class WaitlistLeaveView(APIView):
    """DELETE /api/v1/booking/waitlist/{id}"""

    permission_classes = [IsAuthenticated]

    def delete(self, request: Request, entry_id):
        if not waitlist_services.leave(client_id=request.user.id, entry_id=entry_id):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Bronni boshqa vaqtga ko'chirish",
    request=RescheduleBookingSerializer,
    responses={200: BookingSerializer, 400: DETAIL, 404: None, 409: DETAIL},
)
class RescheduleBookingView(APIView):
    """POST /api/v1/booking/bookings/{id}/reschedule

    Eng ko'p so'raladigan amal: mijozning rejasi o'zgardi, lekin u bekor
    qilishni emas, boshqa vaqtga o'tishni xohlaydi.
    """

    permission_classes = [IsAuthenticated]
    write_throttle_scope = "booking_cancel"  # A7

    def post(self, request: Request, booking_id):
        serializer = RescheduleBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # A4: begona bron — xuddi mavjud emasdek (404), 403 emas
        if not Booking.objects.filter(id=booking_id, client=request.user).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)

        booking = booking_services.reschedule_booking(
            booking_id, serializer.validated_data["new_slot_id"], actor="client"
        )
        return Response(BookingSerializer(booking).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Nizo ochish",
    request=OpenDisputeSerializer,
    responses={201: DisputeSerializer, 404: None, 409: DETAIL},
)
class OpenDisputeView(APIView):
    """POST /api/v1/booking/bookings/{id}/disputes"""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, booking_id):
        serializer = OpenDisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = Booking.objects.filter(id=booking_id, client=request.user).first()
        if booking is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        dispute = disputes.open_dispute(booking=booking, user=request.user, **serializer.validated_data)
        return Response(DisputeSerializer(dispute).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Nizolar navbati (admin)",
    responses={200: DisputeSerializer(many=True)},
)
class AdminDisputeListView(APIView):
    """GET /api/v1/booking/disputes?status=open — SLA bo'yicha tartiblangan"""

    permission_classes = [IsPlatformAdmin]

    def get(self, request: Request):
        qs = Dispute.objects.all().order_by("due_at")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(DisputeSerializer(qs[:200], many=True).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Nizoni hal qilish (admin)",
    request=ResolveDisputeSerializer,
    responses={200: DisputeSerializer, 404: None, 409: DETAIL},
)
class AdminResolveDisputeView(APIView):
    """POST /api/v1/booking/disputes/{id}/resolve"""

    permission_classes = [IsPlatformAdmin]

    def post(self, request: Request, dispute_id):
        serializer = ResolveDisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not Dispute.objects.filter(id=dispute_id).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        dispute = disputes.resolve_dispute(dispute_id, admin=request.user, **serializer.validated_data)
        return Response(DisputeSerializer(dispute).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Bekor qilsam qancha qaytadi (preview)",
    description="Bekor qilish siyosati: >24 soat 100%, 2–24 soat 50%, <2 soat 0%. Qabul boshlangan bo'lsa allowed=false.",
    responses={200: CancellationPreviewSerializer, 404: None},
)
class CancellationPreviewView(APIView):
    """GET /api/v1/booking/bookings/{id}/cancellation-preview"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, booking_id):
        if not Booking.objects.filter(id=booking_id, client=request.user).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        outcome = booking_services.preview_cancellation(booking_id, actor="client")
        return Response(CancellationPreviewSerializer(outcome.__dict__).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Yana bron qilish — tayyor forma",
    description="O'sha shifokor, bemor, manzil, xizmatlar (hozirgi narx bilan) va eng yaqin bo'sh vaqtlar. "
                "Bron yaratilmaydi — keyin odatdagi POST /bookings.",
    responses={200: OpenApiTypes.OBJECT, 404: None},
)
class RebookView(APIView):
    """GET /api/v1/booking/bookings/{id}/rebook (C15.7)"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, booking_id):
        from api.booking.rebook import RebookNotFound, rebook_draft

        try:
            return Response(rebook_draft(client_id=request.user.id, booking_id=booking_id))
        except RebookNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)


class PricePreviewSerializer(serializers.Serializer):
    doctor_id = serializers.UUIDField()
    service_ids = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=10)
    promo_code = serializers.CharField(required=False, allow_blank=True, max_length=32, default="")
    # C12 dinamik narx: slot berilsa narx AYNAN o'sha vaqtga hisoblanadi.
    # Berilmasa — tuzatmasiz bazaviy narx (katalogdagi "dan boshlab" narxi).
    slot_id = serializers.UUIDField(required=False, allow_null=True, default=None)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Narxni oldindan ko'rish (chegirma bilan)",
    description="Promo kodni to'lovdan OLDIN tekshirish. Bron va promo limiti band qilinmaydi.",
    request=PricePreviewSerializer,
    responses={200: OpenApiTypes.OBJECT, 400: DETAIL},
)
class PricePreviewView(APIView):
    """POST /api/v1/booking/price-preview (C12)"""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request):
        from decimal import Decimal

        from api.booking import discounts, time_pricing
        from api.schedule import services as schedule_services

        s = PricePreviewSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        services = catalog_services.get_bookable_services(d["doctor_id"], d["service_ids"])
        if len(services) != len(d["service_ids"]):
            return Response({"detail": "Ba'zi xizmatlar topilmadi yoki faol emas"}, status=400)
        adjust_percent = 0
        if d["slot_id"]:
            slot = schedule_services.get_slot_for_booking(d["slot_id"])
            if slot is None or slot.doctor_id != d["doctor_id"]:
                return Response({"detail": "Slot topilmadi"}, status=400)
            adjust_percent = time_pricing.percent_for_slot(
                d["doctor_id"], slot.start_at, schedule_services.LOCAL_TZ
            )
        prices = time_pricing.adjusted_prices(services, adjust_percent)
        original = sum(prices.values(), Decimal("0"))
        try:
            disc = discounts.best_discount(
                client_id=request.user.id, doctor_id=d["doctor_id"], total=original,
                promo_code=d["promo_code"],
                follow_up_percent=catalog_services.get_follow_up_discount_percent(d["doctor_id"]),
                service_prices=prices,
            )
        except discounts.PromoInvalid as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({
            "original_price": str(original),
            "price_adjust_percent": adjust_percent,
            "discount_amount": str(disc.amount),
            "discount_kind": disc.kind,
            "total_price": str(original - disc.amount),
        })


# ---------------------------------------------------------------------------
# C12: paketlar
# ---------------------------------------------------------------------------


class PackageOfferSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    doctor_id = serializers.UUIDField()
    service_id = serializers.UUIDField()
    service_name = serializers.CharField()
    name = serializers.CharField()
    sessions = serializers.IntegerField()
    discount_percent = serializers.IntegerField()
    validity_days = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_saving = serializers.DecimalField(max_digits=12, decimal_places=2)


class EnrollmentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    doctor_id = serializers.UUIDField()
    service_id = serializers.UUIDField()
    service_name = serializers.CharField()
    package_name = serializers.CharField()
    discount_percent = serializers.IntegerField()
    sessions_total = serializers.IntegerField()
    sessions_left = serializers.IntegerField()
    expires_at = serializers.DateTimeField()


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Shifokorning paketlari (C12)",
    description="Kurs takliflari: N ta qabul — chegirma. Yozilish pul talab qilmaydi.",
    responses={200: PackageOfferSerializer(many=True)},
)
class DoctorPackagesView(APIView):
    """GET /api/v1/booking/doctors/{doctor_id}/packages"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request, doctor_id):
        return Response(PackageOfferSerializer(catalog_services.get_packages(doctor_id), many=True).data)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Paketga yozilish (C12)",
    description=(
        "Pul o'tmaydi: keyingi bronlar chegirmali bo'ladi. Bir xizmat bo'yicha "
        "bir vaqtda bitta faol paket."
    ),
    request=None,
    responses={201: EnrollmentSerializer, 400: DETAIL},
)
class PackageEnrollView(APIView):
    """POST /api/v1/booking/packages/{package_id}/enroll"""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, package_id):
        from api.booking import packages

        try:
            enr = packages.enroll(client_id=request.user.id, package_id=package_id)
        except packages.PackageError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(EnrollmentSerializer(enr).data, status=201)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Mening paketlarim (C12)",
    responses={200: EnrollmentSerializer(many=True)},
)
class MyPackagesView(APIView):
    """GET /api/v1/booking/packages/mine"""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request):
        from api.booking import packages

        return Response(EnrollmentSerializer(packages.my_enrollments(request.user.id), many=True).data)
