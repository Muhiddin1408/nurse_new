"""
clinic/views.py — klinika admini paneli API'si (D7, D8).

Bir foydalanuvchi bir nechta klinikani boshqarishi mumkin: `?clinic_id=`
(yoki tanada `clinic_id`). Bitta klinika bo'lsa — ixtiyoriy.
Begona klinika ID'si — 404.

    GET/PATCH  /api/v1/clinic/profile
    GET        /api/v1/clinic/doctors
    POST       /api/v1/clinic/doctors/invite                         {phone, position}
    POST       /api/v1/clinic/doctors/{affiliation_id}/{pause|resume|end}
    GET        /api/v1/clinic/schedule?date=
    GET/POST   /api/v1/clinic/closures        DELETE /closures/{id}
    GET        /api/v1/clinic/appointments?date=&q=                  (reception)
    GET        /api/v1/clinic/reports?date_from=&date_to=
    GET/POST   /api/v1/clinic/rooms           PATCH /rooms/{id}
    GET        /api/v1/clinic/rules           POST /rules/{id}/room   {room_id|null}

    Shifokor tomoni:
    GET  /api/v1/doctor/clinic-invites
    POST /api/v1/doctor/clinic-invites/{id}/{accept|decline}
    POST /api/v1/doctor/affiliations/{id}/leave
"""

from datetime import date

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.clinic import services as clinic
from api.docs import DETAIL, TAG_CLINIC, TAG_DOCTOR
from api.permissions import IsClinicAdmin, IsDoctor
from apps.catalog.models import Clinic, Room

CLINIC_PARAM = OpenApiParameter("clinic_id", OpenApiTypes.UUID, description="Bir nechta klinika bo'lsa majburiy")


class _NoClinic(Exception):
    pass


def _clinic_id(request):
    raw = request.query_params.get("clinic_id") or (request.data.get("clinic_id") if hasattr(request.data, "get") else None)
    ids = {str(i): i for i in request.clinic_ids}
    if raw:
        if str(raw) not in ids:
            raise _NoClinic
        return ids[str(raw)]
    if len(ids) == 1:
        return next(iter(ids.values()))
    raise clinic.ClinicError("clinic_id majburiy — siz bir nechta klinikani boshqarasiz")


def _handle(fn):
    try:
        return fn()
    except (_NoClinic, clinic.NotFound) as exc:
        detail = str(exc) if isinstance(exc, clinic.NotFound) and str(exc) != "not_found" else None
        return Response({"detail": detail} if detail else None, status=status.HTTP_404_NOT_FOUND)
    except clinic.ClinicError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)


def _date_param(request, name, *, required=True):
    raw = request.query_params.get(name)
    if not raw:
        if required:
            raise clinic.ClinicError(f"{name} majburiy (YYYY-MM-DD)")
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise clinic.ClinicError(f"{name}: YYYY-MM-DD") from None


class _ClinicView(APIView):
    permission_classes = [IsClinicAdmin]


# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------


class ClinicProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clinic
        fields = ["id", "name", "description", "phone", "city", "street", "latitude", "longitude",
                  "status", "rating", "reviews_count", "working_hours", "photo_url"]
        read_only_fields = ["id", "name", "city", "street", "latitude", "longitude", "status",
                            "rating", "reviews_count"]


class ClinicProfileView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Klinika profili", parameters=[CLINIC_PARAM],
                   responses={200: ClinicProfileSerializer, 404: None})
    def get(self, request):
        return _handle(lambda: Response(ClinicProfileSerializer(Clinic.objects.get(id=_clinic_id(request))).data))

    @extend_schema(tags=[TAG_CLINIC], summary="Profilni tahrirlash (tavsif, telefon, ish vaqti, foto)",
                   parameters=[CLINIC_PARAM], request=ClinicProfileSerializer,
                   responses={200: ClinicProfileSerializer, 409: DETAIL})
    def patch(self, request):
        def run():
            data = {k: v for k, v in request.data.items() if k != "clinic_id"}
            s = ClinicProfileSerializer(data=data, partial=True)
            s.is_valid(raise_exception=True)
            unknown = set(data) - set(clinic.PROFILE_FIELDS)
            if unknown:
                raise clinic.ClinicError(f"Bu maydonlar moderatsiyasiz o'zgarmaydi: {', '.join(sorted(unknown))}")
            c = clinic.update_profile(_clinic_id(request), s.validated_data, user=request.user)
            return Response(ClinicProfileSerializer(c).data)
        return _handle(run)


# ---------------------------------------------------------------------------
# Shifokorlar
# ---------------------------------------------------------------------------


def _affiliation_row(a) -> dict:
    return {
        "affiliation_id": str(a.id), "doctor_id": str(a.doctor_id), "doctor": a.doctor.user.full_name,
        "specializations": [s.name for s in a.doctor.specializations.all()],
        "position": a.position, "status": a.status, "doctor_status": a.doctor.status,
        "rating": str(a.doctor.rating), "reviews_count": a.doctor.reviews_count,
    }


class InviteSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    position = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class ClinicDoctorsView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Klinika shifokorlari", parameters=[CLINIC_PARAM],
                   responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return _handle(lambda: Response([_affiliation_row(a) for a in clinic.list_doctors(_clinic_id(request))]))


class ClinicInviteView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Shifokorni taklif qilish (shifokor qabul qilishi kerak)",
                   parameters=[CLINIC_PARAM], request=InviteSerializer,
                   responses={201: OpenApiTypes.OBJECT, 404: DETAIL, 409: DETAIL})
    def post(self, request):
        s = InviteSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return _handle(lambda: Response(
            _affiliation_row(clinic.invite(_clinic_id(request), user=request.user, **s.validated_data)),
            status=status.HTTP_201_CREATED))


class ClinicAffiliationActionView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Shifokorni to'xtatish / qaytarish / chiqarish",
                   parameters=[CLINIC_PARAM], request=None, responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
    def post(self, request, affiliation_id, action):
        def run():
            aff, kept = clinic.change_affiliation(_clinic_id(request), affiliation_id, action, user=request.user)
            return Response({"affiliation_id": str(aff.id), "status": aff.status, "active_bookings_kept": kept})
        return _handle(run)


# ---------------------------------------------------------------------------
# Jadval va dam olish kunlari
# ---------------------------------------------------------------------------


class ClinicScheduleView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Barcha shifokorlar jadvali (bir kun)",
                   parameters=[CLINIC_PARAM, OpenApiParameter("date", OpenApiTypes.DATE, required=True)],
                   responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return _handle(lambda: Response(clinic.schedule_overview(_clinic_id(request), _date_param(request, "date"))))


class ClosureSerializer(serializers.Serializer):
    date = serializers.DateField()
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class ClinicClosuresView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Klinika dam olish kunlari", parameters=[CLINIC_PARAM],
                   responses=OpenApiTypes.OBJECT)
    def get(self, request):
        from apps.schedule.models import ClinicClosure

        def run():
            rows = ClinicClosure.objects.filter(clinic_id=_clinic_id(request)).order_by("date")
            return Response([{"id": str(c.id), "date": c.date.isoformat(), "reason": c.reason} for c in rows])
        return _handle(run)

    @extend_schema(tags=[TAG_CLINIC], summary="Dam olish kuni qo'shish (bo'sh slotlar yopiladi)",
                   parameters=[CLINIC_PARAM], request=ClosureSerializer,
                   responses={201: OpenApiTypes.OBJECT, 409: DETAIL})
    def post(self, request):
        s = ClosureSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        def run():
            closure, conflicts = clinic.create_closure(_clinic_id(request), day=s.validated_data["date"],
                                                       reason=s.validated_data["reason"], user=request.user)
            # Faol bronlar bekor qilinmaydi — reception ularni bemor bilan hal qiladi
            return Response({"id": str(closure.id), "date": closure.date.isoformat(),
                             "bookings_to_reschedule": conflicts}, status=status.HTTP_201_CREATED)
        return _handle(run)


class ClinicClosureDetailView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Dam olish kunini bekor qilish", parameters=[CLINIC_PARAM],
                   responses={204: None, 404: None})
    def delete(self, request, closure_id):
        def run():
            clinic.delete_closure(_clinic_id(request), closure_id, user=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        return _handle(run)


# ---------------------------------------------------------------------------
# Qabullar va hisobot
# ---------------------------------------------------------------------------


class ClinicAppointmentsView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Qabullar (reception): kun + qidiruv (telefon / ism / raqam)",
                   parameters=[CLINIC_PARAM, OpenApiParameter("date", OpenApiTypes.DATE),
                               OpenApiParameter("q", OpenApiTypes.STR)],
                   responses=OpenApiTypes.OBJECT)
    def get(self, request):
        from django.utils import timezone

        from api.schedule.services import LOCAL_TZ

        def run():
            day = _date_param(request, "date", required=False) or timezone.localtime(timezone.now(), LOCAL_TZ).date()
            return Response(clinic.appointments(_clinic_id(request), day, request.query_params.get("q", "")))
        return _handle(run)


class ClinicReportView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Hisobot: daromad, bandlik, no-show, shifokorlar kesimi",
                   parameters=[CLINIC_PARAM, OpenApiParameter("date_from", OpenApiTypes.DATE, required=True),
                               OpenApiParameter("date_to", OpenApiTypes.DATE, required=True)],
                   responses={200: OpenApiTypes.OBJECT, 409: DETAIL})
    def get(self, request):
        return _handle(lambda: Response(clinic.report(
            _clinic_id(request), _date_param(request, "date_from"), _date_param(request, "date_to"))))


# ---------------------------------------------------------------------------
# D8 — xonalar
# ---------------------------------------------------------------------------


class RoomSerializer(serializers.ModelSerializer):
    equipment = serializers.ListField(child=serializers.CharField(max_length=50), required=False, max_length=30)

    class Meta:
        model = Room
        fields = ["id", "name", "floor", "equipment", "is_active"]
        read_only_fields = ["id"]


class ClinicRoomsView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Xonalar", parameters=[CLINIC_PARAM], responses=RoomSerializer(many=True))
    def get(self, request):
        return _handle(lambda: Response(RoomSerializer(
            Room.objects.filter(clinic_id=_clinic_id(request)).order_by("name"), many=True).data))

    @extend_schema(tags=[TAG_CLINIC], summary="Xona qo'shish", parameters=[CLINIC_PARAM], request=RoomSerializer,
                   responses={201: RoomSerializer, 409: DETAIL})
    def post(self, request):
        s = RoomSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return _handle(lambda: Response(RoomSerializer(
            clinic.create_room(_clinic_id(request), dict(s.validated_data), user=request.user)).data,
            status=status.HTTP_201_CREATED))


class ClinicRoomDetailView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Xonani tahrirlash / o'chirish (is_active=false)",
                   parameters=[CLINIC_PARAM], request=RoomSerializer, responses={200: RoomSerializer, 404: None})
    def patch(self, request, room_id):
        s = RoomSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        return _handle(lambda: Response(RoomSerializer(
            clinic.update_room(_clinic_id(request), room_id, dict(s.validated_data), user=request.user)).data))


class ClinicRulesView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Klinikadagi ish qoidalari (xona biriktirish uchun)",
                   parameters=[CLINIC_PARAM], responses=OpenApiTypes.OBJECT)
    def get(self, request):
        def run():
            return Response([
                {"rule_id": str(r.id), "doctor": r.doctor.user.full_name, "weekday": r.weekday,
                 "start_time": r.start_time.isoformat(), "end_time": r.end_time.isoformat(),
                 "room_id": str(r.room_id) if r.room_id else None, "room": r.room.name if r.room_id else None}
                for r in clinic.clinic_rules(_clinic_id(request))
            ])
        return _handle(run)


class AssignRoomSerializer(serializers.Serializer):
    room_id = serializers.UUIDField(allow_null=True)


class ClinicRuleRoomView(_ClinicView):
    @extend_schema(tags=[TAG_CLINIC], summary="Ish qoidasiga xona biriktirish (kelajak slotlariga ham)",
                   parameters=[CLINIC_PARAM], request=AssignRoomSerializer,
                   responses={200: OpenApiTypes.OBJECT, 404: None})
    def post(self, request, rule_id):
        s = AssignRoomSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return _handle(lambda: Response(clinic.assign_room(
            _clinic_id(request), rule_id, s.validated_data["room_id"], user=request.user)))


# ---------------------------------------------------------------------------
# Shifokor tomoni — takliflar
# ---------------------------------------------------------------------------


class DoctorInvitesView(APIView):
    permission_classes = [IsDoctor]

    @extend_schema(tags=[TAG_DOCTOR], summary="Klinikalardan kelgan takliflar", responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response([
            {"affiliation_id": str(a.id), "clinic_id": str(a.clinic_id), "clinic": a.clinic.name,
             "city": a.clinic.city, "street": a.clinic.street, "position": a.position,
             "invited_at": a.updated_at.isoformat()}
            for a in clinic.doctor_invites(request.doctor)
        ])


class DoctorInviteRespondView(APIView):
    permission_classes = [IsDoctor]

    @extend_schema(tags=[TAG_DOCTOR], summary="Taklifni qabul qilish / rad etish", request=None,
                   responses={200: OpenApiTypes.OBJECT, 404: None})
    def post(self, request, affiliation_id, action):
        if action not in ("accept", "decline"):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return _handle(lambda: Response({"status": clinic.respond_invite(
            request.doctor, affiliation_id, accept=action == "accept").status}))


class DoctorLeaveClinicView(APIView):
    permission_classes = [IsDoctor]

    @extend_schema(tags=[TAG_DOCTOR], summary="Klinikadan chiqish", request=None,
                   responses={200: OpenApiTypes.OBJECT, 404: None})
    def post(self, request, affiliation_id):
        return _handle(lambda: Response({"status": clinic.leave_clinic(request.doctor, affiliation_id).status}))
