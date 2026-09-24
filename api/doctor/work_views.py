"""
doctor/work_views.py — qabullar (D4), xizmatlar va narxlar (D5), ochiq profil.

    GET  /api/v1/doctor/appointments/today
    GET  /api/v1/doctor/appointments?from=&to=&status=
    GET  /api/v1/doctor/appointments/{id}
    POST /api/v1/doctor/appointments/{id}/start | complete | no-show | cancel

    GET/POST        /api/v1/doctor/services
    PATCH/DELETE    /api/v1/doctor/services/{id}
    PATCH           /api/v1/doctor/services/{id}/toggle
    GET             /api/v1/doctor/services/{id}/price-history
    PATCH           /api/v1/doctor/profile

    GET/POST        /api/v1/clinic/services           (klinika admini)
    PATCH/DELETE    /api/v1/clinic/services/{id}
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.booking.services import BookingError
from api.docs import DETAIL, TAG_CLINIC, TAG_DOCTOR_WORK
from api.doctor import appointments, offerings
from api.permissions import IsApprovedDoctor, IsClinicAdmin
from apps.catalog.models import Service


def _bad(exc, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": str(exc)}, status=code)


# ---------------------------------------------------------------------------
# D4 — qabullar
# ---------------------------------------------------------------------------


class NoteSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=4000, default="")


class CancelReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000)


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Bugungi qabullar", responses=OpenApiTypes.OBJECT)
class TodayAppointmentsView(APIView):
    permission_classes = [IsApprovedDoctor]

    def get(self, request):
        return Response([appointments.serialize(b) for b in appointments.today(request.doctor)])


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Qabullar ro'yxati (filtr bilan)",
               parameters=[OpenApiParameter("from", OpenApiTypes.DATE, required=True),
                           OpenApiParameter("to", OpenApiTypes.DATE, required=True),
                           OpenApiParameter("status", OpenApiTypes.STR,
                                            enum=["confirmed", "completed", "no_show", "cancelled"])],
               responses={200: OpenApiTypes.OBJECT, 400: DETAIL})
class AppointmentListView(APIView):
    permission_classes = [IsApprovedDoctor]

    def get(self, request):
        f = serializers.DateField()
        try:
            date_from = f.to_internal_value(request.query_params.get("from"))
            date_to = f.to_internal_value(request.query_params.get("to"))
            rows = appointments.list_range(request.doctor, date_from, date_to, request.query_params.get("status"))
        except serializers.ValidationError:
            return _bad("from va to: YYYY-MM-DD")
        except appointments.AppointmentError as exc:
            return _bad(exc)
        return Response([appointments.serialize(b) for b in rows])


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Qabul tafsiloti + bemorning shu shifokordagi tarixi",
               responses={200: OpenApiTypes.OBJECT, 404: None})
class AppointmentDetailView(APIView):
    permission_classes = [IsApprovedDoctor]

    def get(self, request, booking_id):
        try:
            b = appointments.get_owned(request.doctor, booking_id)
        except appointments.AppointmentError:
            return Response(status=status.HTTP_404_NOT_FOUND)
        history = appointments.patient_history(request.doctor, b)
        return Response(appointments.serialize(b, detail=True, history=history))


class _AppointmentActionView(APIView):
    permission_classes = [IsApprovedDoctor]
    serializer_class = None

    def perform(self, request, booking_id, data):  # pragma: no cover
        raise NotImplementedError

    def post(self, request, booking_id):
        data = {}
        if self.serializer_class:
            s = self.serializer_class(data=request.data)
            s.is_valid(raise_exception=True)
            data = s.validated_data
        try:
            b = self.perform(request, booking_id, data)
        except appointments.AppointmentError as exc:
            if str(exc) == "not_found":
                return Response(status=status.HTTP_404_NOT_FOUND)
            return _bad(exc, status.HTTP_409_CONFLICT)
        except BookingError as exc:
            return _bad(exc, status.HTTP_409_CONFLICT)
        b = appointments.get_owned(request.doctor, b.id)
        return Response(appointments.serialize(b))


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Qabul boshlandi", request=None,
               responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
class AppointmentStartView(_AppointmentActionView):
    def perform(self, request, booking_id, data):
        return appointments.start(request.doctor, booking_id)


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Qabul yakunlandi (izoh / tavsiya bilan)", request=NoteSerializer,
               responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
class AppointmentCompleteView(_AppointmentActionView):
    serializer_class = NoteSerializer

    def perform(self, request, booking_id, data):
        return appointments.complete(request.doctor, booking_id, data["note"])


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Mijoz kelmadi", request=NoteSerializer,
               responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
class AppointmentNoShowView(_AppointmentActionView):
    serializer_class = NoteSerializer

    def perform(self, request, booking_id, data):
        return appointments.no_show(request.doctor, booking_id, data["note"])


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Qabulni bekor qilish (mijozga 100% refund)",
               request=CancelReasonSerializer, responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
class AppointmentCancelView(_AppointmentActionView):
    serializer_class = CancelReasonSerializer

    def perform(self, request, booking_id, data):
        return appointments.cancel(request.doctor, booking_id, data["reason"])


# ---------------------------------------------------------------------------
# D5 — xizmatlar
# ---------------------------------------------------------------------------


class ServiceWriteSerializer(serializers.Serializer):
    specialization_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    name_ru = serializers.CharField(max_length=255, required=False, allow_blank=True)  # C14
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    place = serializers.ChoiceField(choices=Service.Place.choices)
    duration_minutes = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)
    confirm_large_change = serializers.BooleanField(required=False, default=False, write_only=True)


class ServiceReadSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    specialization_id = serializers.UUIDField()
    name = serializers.CharField()
    name_ru = serializers.CharField()
    description = serializers.CharField()
    place = serializers.CharField()
    duration_minutes = serializers.IntegerField()
    price = serializers.DecimalField(max_digits=12, decimal_places=2)
    is_active = serializers.BooleanField()


class PriceHistorySerializer(serializers.Serializer):
    old_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    new_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    is_large_change = serializers.BooleanField()
    created_at = serializers.DateTimeField()


class _ServicesBase(APIView):
    """Egasi: shifokor (shaxsiy xizmatlar) yoki klinika admini (klinika xizmatlari)."""

    def owned_qs(self, request):  # pragma: no cover
        raise NotImplementedError

    def create_kwargs(self, request, data):  # pragma: no cover
        raise NotImplementedError

    def get_owned(self, request, service_id):
        return self.owned_qs(request).filter(id=service_id).first()

    def list(self, request):
        return Response(ServiceReadSerializer(self.owned_qs(request).order_by("place", "price"), many=True).data)

    def create(self, request):
        s = ServiceWriteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        data = dict(s.validated_data)
        data.pop("confirm_large_change")
        try:
            service = offerings.create(**self.create_kwargs(request, data))
        except offerings.OfferingError as exc:
            return _bad(exc)
        return Response(ServiceReadSerializer(service).data, status=status.HTTP_201_CREATED)

    def patch_one(self, request, service_id):
        service = self.get_owned(request, service_id)
        if service is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        s = ServiceWriteSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        data = dict(s.validated_data)
        confirm = data.pop("confirm_large_change", False)
        try:
            service = offerings.update(service, data, user=request.user, confirm_large_change=confirm)
        except offerings.LargePriceChange as exc:
            return Response({"detail": str(exc), "code": "large_price_change",
                             "old_price": str(exc.old), "new_price": str(exc.new)}, status=status.HTTP_409_CONFLICT)
        except offerings.OfferingError as exc:
            return _bad(exc)
        return Response(ServiceReadSerializer(service).data)

    def delete_one(self, request, service_id):
        service = self.get_owned(request, service_id)
        if service is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({"result": offerings.delete(service)})


class DoctorServicesView(_ServicesBase):
    permission_classes = [IsApprovedDoctor]

    def owned_qs(self, request):
        return Service.objects.filter(doctor=request.doctor)

    def create_kwargs(self, request, data):
        return {"owner_doctor": request.doctor, "data": data}

    @extend_schema(tags=[TAG_DOCTOR_WORK], summary="Mening xizmatlarim", responses=ServiceReadSerializer(many=True))
    def get(self, request):
        return self.list(request)

    @extend_schema(tags=[TAG_DOCTOR_WORK], summary="Xizmat qo'shish", request=ServiceWriteSerializer,
                   responses={201: ServiceReadSerializer, 400: DETAIL})
    def post(self, request):
        return self.create(request)


class DoctorServiceDetailView(_ServicesBase):
    permission_classes = [IsApprovedDoctor]
    owned_qs = DoctorServicesView.owned_qs

    @extend_schema(tags=[TAG_DOCTOR_WORK], summary="Xizmat / narxni o'zgartirish (>50% oshirish tasdiq talab qiladi)",
                   request=ServiceWriteSerializer, responses={200: ServiceReadSerializer, 404: None, 409: DETAIL})
    def patch(self, request, service_id):
        return self.patch_one(request, service_id)

    @extend_schema(tags=[TAG_DOCTOR_WORK], summary="Xizmatni o'chirish (bronda ishlatilgan bo'lsa — faqat o'chirib qo'yiladi)",
                   responses={200: OpenApiTypes.OBJECT, 404: None})
    def delete(self, request, service_id):
        return self.delete_one(request, service_id)


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Xizmatni vaqtincha yoqish / o'chirish", request=None,
               responses={200: ServiceReadSerializer, 404: None})
class DoctorServiceToggleView(APIView):
    permission_classes = [IsApprovedDoctor]

    def patch(self, request, service_id):
        service = Service.objects.filter(id=service_id, doctor=request.doctor).first()
        if service is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(ServiceReadSerializer(offerings.toggle(service)).data)


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Narx tarixi", responses={200: PriceHistorySerializer(many=True)})
class DoctorServicePriceHistoryView(APIView):
    permission_classes = [IsApprovedDoctor]

    def get(self, request, service_id):
        service = Service.objects.filter(id=service_id, doctor=request.doctor).first()
        if service is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(PriceHistorySerializer(service.price_history.order_by("-created_at"), many=True).data)


class PublicProfileSerializer(serializers.Serializer):
    bio = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    education = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    languages = serializers.ListField(child=serializers.CharField(max_length=5), required=False, max_length=6)
    accepts_home_visits = serializers.BooleanField(required=False)
    home_visit_radius_km = serializers.IntegerField(required=False, min_value=1, max_value=100)
    # C12: 14 kun ichida qayta qabul chegirmasi (shifokor o'zi to'laydi)
    follow_up_discount_percent = serializers.IntegerField(required=False, min_value=0, max_value=100)
    # C4: radius shu nuqtadan o'lchanadi (uy chaqiruviga chiqish joyi)
    home_base_latitude = serializers.DecimalField(
        required=False, allow_null=True, max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    home_base_longitude = serializers.DecimalField(
        required=False, allow_null=True, max_digits=9, decimal_places=6, min_value=-180, max_value=180)
    # B10: klinikadagi qabul uchun to'lov rejimi (uy chaqiruvi bunga
    # bo'ysunmaydi — u har doim to'liq oldindan)
    clinic_payment_mode = serializers.ChoiceField(
        choices=["prepaid", "deposit", "at_clinic"], required=False
    )


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Ochiq profilni tahrirlash (moderatsiyasiz maydonlar)",
               request=PublicProfileSerializer, responses={200: PublicProfileSerializer, 400: DETAIL})
class DoctorPublicProfileView(APIView):
    permission_classes = [IsApprovedDoctor]

    def patch(self, request):
        unknown = set(request.data) - set(PublicProfileSerializer().fields)
        if unknown:
            return _bad(f"Bu maydonlar moderatsiyasiz o'zgarmaydi: {', '.join(sorted(unknown))}")
        s = PublicProfileSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        doctor = offerings.update_public_profile(request.doctor, s.validated_data)
        return Response(PublicProfileSerializer(doctor).data)


# ---------------------------------------------------------------------------
# Klinika admini — klinika xizmatlari
# ---------------------------------------------------------------------------


class ClinicServicesView(_ServicesBase):
    permission_classes = [IsClinicAdmin]

    def owned_qs(self, request):
        return Service.objects.filter(clinic_id__in=request.clinic_ids, doctor__isnull=True)

    def create_kwargs(self, request, data):
        clinic_id = request.data.get("clinic_id") or (next(iter(request.clinic_ids)) if len(request.clinic_ids) == 1 else None)
        if clinic_id is None or str(clinic_id) not in {str(c) for c in request.clinic_ids}:
            raise offerings.OfferingError("clinic_id noto'g'ri")
        return {"owner_clinic_id": clinic_id, "data": data}

    def create(self, request):
        try:
            return super().create(request)
        except offerings.OfferingError as exc:
            return _bad(exc)

    @extend_schema(tags=[TAG_CLINIC], summary="Klinika xizmatlari", responses=ServiceReadSerializer(many=True))
    def get(self, request):
        return self.list(request)

    @extend_schema(tags=[TAG_CLINIC], summary="Klinika xizmati qo'shish", request=ServiceWriteSerializer,
                   responses={201: ServiceReadSerializer, 400: DETAIL})
    def post(self, request):
        return self.create(request)


class ClinicServiceDetailView(_ServicesBase):
    permission_classes = [IsClinicAdmin]
    owned_qs = ClinicServicesView.owned_qs

    @extend_schema(tags=[TAG_CLINIC], summary="Klinika xizmatini o'zgartirish", request=ServiceWriteSerializer,
                   responses={200: ServiceReadSerializer, 404: None, 409: DETAIL})
    def patch(self, request, service_id):
        return self.patch_one(request, service_id)

    @extend_schema(tags=[TAG_CLINIC], summary="Klinika xizmatini o'chirish", responses={200: OpenApiTypes.OBJECT})
    def delete(self, request, service_id):
        return self.delete_one(request, service_id)
