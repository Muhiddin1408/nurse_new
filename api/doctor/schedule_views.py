"""
doctor/schedule_views.py — jadval endpointlari (D3), faqat tasdiqlangan shifokor.

    GET/POST        /api/v1/doctor/working-rules
    PATCH/DELETE    /api/v1/doctor/working-rules/{id}?resolution=
    GET/POST        /api/v1/doctor/time-off
    DELETE          /api/v1/doctor/time-off/{id}
    POST            /api/v1/doctor/slots/{id}/block
    POST            /api/v1/doctor/slots/{id}/unblock
    GET             /api/v1/doctor/schedule?from=&to=
    POST            /api/v1/doctor/schedule/regenerate

To'qnashuv: 409 {"detail", "conflicts": [...], "options": ["cancel_and_refund", "keep_bookings", "choose_other_dates"]}
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.docs import DETAIL, TAG_DOCTOR_SCHEDULE
from api.doctor import schedule
from api.permissions import IsApprovedDoctor
from apps.schedule.models import TimeOff


class WorkingRuleSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    slot_minutes = serializers.IntegerField()
    valid_from = serializers.DateField()
    valid_to = serializers.DateField(required=False, allow_null=True)
    clinic_id = serializers.UUIDField(required=False, allow_null=True)


class TimeOffSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    kind = serializers.ChoiceField(choices=TimeOff.Kind.choices, default=TimeOff.Kind.OTHER)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")


class ResolutionSerializer(serializers.Serializer):
    resolution = serializers.ChoiceField(choices=schedule.RESOLUTIONS, required=False, allow_null=True)


class BlockSlotSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")


CONFLICT = inline_serializer("ScheduleConflict", fields={
    "detail": serializers.CharField(),
    "conflicts": serializers.ListField(child=serializers.DictField()),
    "options": serializers.ListField(child=serializers.CharField()),
})
RESOLUTION_PARAM = OpenApiParameter(
    "resolution", OpenApiTypes.STR, enum=list(schedule.RESOLUTIONS),
    description="To'qnashuv bo'lsa qanday hal qilinadi (409 javobidan keyin yuboriladi)",
)


def _conflict(exc: schedule.ScheduleConflict) -> Response:
    return Response(
        {"detail": str(exc), "conflicts": exc.conflicts,
         "options": [schedule.RESOLUTION_CANCEL, schedule.RESOLUTION_KEEP, "choose_other_dates"]},
        status=status.HTTP_409_CONFLICT,
    )


def _bad(exc) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _resolution(request):
    s = ResolutionSerializer(data={"resolution": request.query_params.get("resolution")
                                   or (request.data.get("resolution") if hasattr(request.data, "get") else None)})
    s.is_valid(raise_exception=True)
    return s.validated_data.get("resolution")


def _change(result: schedule.ChangeResult) -> dict:
    return {"cancelled_bookings": result.cancelled_bookings, "kept_bookings": result.kept_bookings}


class WorkingRulesView(APIView):
    permission_classes = [IsApprovedDoctor]

    @extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Ish qoidalari", responses=WorkingRuleSerializer(many=True))
    def get(self, request):
        return Response(WorkingRuleSerializer(schedule.list_rules(request.doctor), many=True).data)

    @extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Ish qoidasi qo'shish (slotlar avtomatik yaratiladi)",
                   request=WorkingRuleSerializer, responses={201: WorkingRuleSerializer, 400: DETAIL})
    def post(self, request):
        s = WorkingRuleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            rule = schedule.create_rule(request.doctor, s.validated_data)
        except schedule.ScheduleError as exc:
            return _bad(exc)
        return Response(WorkingRuleSerializer(rule).data, status=status.HTTP_201_CREATED)


class WorkingRuleDetailView(APIView):
    permission_classes = [IsApprovedDoctor]

    @extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Ish qoidasini o'zgartirish", parameters=[RESOLUTION_PARAM],
                   request=WorkingRuleSerializer, responses={200: WorkingRuleSerializer, 400: DETAIL, 409: CONFLICT})
    def patch(self, request, rule_id):
        s = WorkingRuleSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        try:
            rule, result = schedule.update_rule(request.doctor, rule_id, s.validated_data, _resolution(request))
        except schedule.ScheduleConflict as exc:
            return _conflict(exc)
        except schedule.ScheduleError as exc:
            return _bad(exc)
        return Response({**WorkingRuleSerializer(rule).data, **_change(result)})

    @extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Ish qoidasini o'chirish", parameters=[RESOLUTION_PARAM],
                   responses={200: OpenApiTypes.OBJECT, 400: DETAIL, 409: CONFLICT})
    def delete(self, request, rule_id):
        try:
            result = schedule.delete_rule(request.doctor, rule_id, _resolution(request))
        except schedule.ScheduleConflict as exc:
            return _conflict(exc)
        except schedule.ScheduleError as exc:
            return _bad(exc)
        return Response(_change(result))


class TimeOffListView(APIView):
    permission_classes = [IsApprovedDoctor]

    @extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Ta'til / dam olishlar", responses=TimeOffSerializer(many=True))
    def get(self, request):
        from django.utils import timezone

        qs = TimeOff.objects.filter(doctor=request.doctor, end_at__gte=timezone.now()).order_by("start_at")
        return Response(TimeOffSerializer(qs, many=True).data)

    @extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Ta'til qo'yish", parameters=[RESOLUTION_PARAM],
                   request=TimeOffSerializer, responses={201: TimeOffSerializer, 400: DETAIL, 409: CONFLICT})
    def post(self, request):
        s = TimeOffSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            time_off, result = schedule.create_time_off(request.doctor, resolution=_resolution(request),
                                                        **s.validated_data)
        except schedule.ScheduleConflict as exc:
            return _conflict(exc)
        except schedule.ScheduleError as exc:
            return _bad(exc)
        return Response({**TimeOffSerializer(time_off).data, **_change(result)}, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Ta'tilni bekor qilish (yopilgan slotlar qayta ochiladi)",
               responses={200: OpenApiTypes.OBJECT, 400: DETAIL})
class TimeOffDetailView(APIView):
    permission_classes = [IsApprovedDoctor]

    def delete(self, request, time_off_id):
        try:
            reopened = schedule.delete_time_off(request.doctor, time_off_id)
        except schedule.ScheduleError as exc:
            return _bad(exc)
        return Response({"reopened_slots": reopened})


@extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Bitta slotni yopish", request=BlockSlotSerializer,
               responses={200: OpenApiTypes.OBJECT, 400: DETAIL})
class BlockSlotView(APIView):
    permission_classes = [IsApprovedDoctor]

    def post(self, request, slot_id):
        s = BlockSlotSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            slot = schedule.block_slot(request.doctor, slot_id, s.validated_data["note"])
        except schedule.ScheduleError as exc:
            return _bad(exc)
        return Response({"slot_id": str(slot.id), "status": "blocked"})


@extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Qo'lda yopilgan slotni ochish", request=None,
               responses={200: OpenApiTypes.OBJECT, 400: DETAIL})
class UnblockSlotView(APIView):
    permission_classes = [IsApprovedDoctor]

    def post(self, request, slot_id):
        try:
            slot = schedule.unblock_slot(request.doctor, slot_id)
        except schedule.ScheduleError as exc:
            return _bad(exc)
        return Response({"slot_id": str(slot.id), "status": "free"})


@extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Kalendar: slotlar + bronlar",
               parameters=[OpenApiParameter("from", OpenApiTypes.DATE, required=True),
                           OpenApiParameter("to", OpenApiTypes.DATE, required=True)],
               responses={200: OpenApiTypes.OBJECT, 400: DETAIL})
class CalendarView(APIView):
    permission_classes = [IsApprovedDoctor]

    def get(self, request):
        f = serializers.DateField()
        try:
            date_from = f.to_internal_value(request.query_params.get("from"))
            date_to = f.to_internal_value(request.query_params.get("to"))
            return Response(schedule.calendar(request.doctor, date_from, date_to))
        except serializers.ValidationError:
            return Response({"detail": "from va to: YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)
        except schedule.ScheduleError as exc:
            return _bad(exc)


@extend_schema(tags=[TAG_DOCTOR_SCHEDULE], summary="Slotlarni qoidalardan qayta yaratish", request=None,
               responses={200: OpenApiTypes.OBJECT})
class RegenerateView(APIView):
    permission_classes = [IsApprovedDoctor]

    def post(self, request):
        return Response(schedule.regenerate(request.doctor.id))
