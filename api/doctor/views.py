"""
doctor/views.py — shifokor tomoni: onboarding (D2) va platforma moderatsiyasi.

    POST   /api/v1/doctor/onboarding/start
    GET    /api/v1/doctor/onboarding/profile
    PATCH  /api/v1/doctor/onboarding/profile
    GET    /api/v1/doctor/onboarding/documents
    POST   /api/v1/doctor/onboarding/documents          (multipart)
    DELETE /api/v1/doctor/onboarding/documents/{id}
    POST   /api/v1/doctor/onboarding/submit
    GET    /api/v1/doctor/onboarding/status
    GET    /api/v1/doctor/documents/download/{token}   (imzolangan, 5 daqiqa)

    GET    /api/v1/moderation/doctors?status=pending
    GET    /api/v1/moderation/doctors/{id}
    POST   /api/v1/moderation/doctors/{id}/approve | reject | suspend | reinstate
"""

from django.http import FileResponse, Http404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.docs import DETAIL, TAG_DOCTOR, TAG_MODERATION
from api.doctor import onboarding
from api.doctor.serializers import (
    DocumentSerializer,
    DocumentUploadSerializer,
    DoctorProfileSerializer,
    ModerationDoctorSerializer,
    OnboardingStatusSerializer,
    ReasonSerializer,
)
from api.permissions import IsDoctor, IsPlatformAdmin
from apps.catalog.models import Doctor, DoctorDocument


def _error(exc) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------


@extend_schema(tags=[TAG_DOCTOR], summary="Shifokor sifatida ro'yxatdan o'tishni boshlash",
               request=None, responses={200: DoctorProfileSerializer, 201: DoctorProfileSerializer})
class OnboardingStartView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        existed = Doctor.objects.filter(user=request.user).exists()
        doctor = onboarding.start(request.user)
        return Response(DoctorProfileSerializer(doctor).data,
                        status=status.HTTP_200_OK if existed else status.HTTP_201_CREATED)


class OnboardingProfileView(APIView):
    permission_classes = [IsDoctor]

    @extend_schema(tags=[TAG_DOCTOR], summary="Onboarding profili", responses=DoctorProfileSerializer)
    def get(self, request):
        return Response(DoctorProfileSerializer(request.doctor).data)

    @extend_schema(tags=[TAG_DOCTOR], summary="Profilni to'ldirish (qoralama / rad etilgan holatda)",
                   request=DoctorProfileSerializer, responses={200: DoctorProfileSerializer, 400: DETAIL})
    def patch(self, request):
        serializer = DoctorProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            doctor = onboarding.update_profile(request.doctor, serializer.validated_data)
        except onboarding.OnboardingError as exc:
            return _error(exc)
        return Response(DoctorProfileSerializer(doctor).data)


class OnboardingDocumentsView(APIView):
    permission_classes = [IsDoctor]
    parser_classes = [MultiPartParser]

    @extend_schema(tags=[TAG_DOCTOR], summary="Yuklangan hujjatlar", responses=DocumentSerializer(many=True))
    def get(self, request):
        docs = request.doctor.documents.order_by("created_at")
        return Response(DocumentSerializer(docs, many=True, context={"request": request}).data)

    @extend_schema(tags=[TAG_DOCTOR], summary="Hujjat yuklash (PDF/JPEG/PNG, max 10 MB)",
                   request={"multipart/form-data": DocumentUploadSerializer},
                   responses={201: DocumentSerializer, 400: DETAIL})
    def post(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            doc = onboarding.add_document(request.doctor, serializer.validated_data["kind"],
                                          serializer.validated_data["file"])
        except onboarding.OnboardingError as exc:
            return _error(exc)
        return Response(DocumentSerializer(doc, context={"request": request}).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG_DOCTOR], summary="Hujjatni o'chirish", responses={204: None, 400: DETAIL})
class OnboardingDocumentDeleteView(APIView):
    permission_classes = [IsDoctor]

    def delete(self, request, document_id):
        try:
            onboarding.delete_document(request.doctor, document_id)
        except onboarding.OnboardingError as exc:
            return _error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=[TAG_DOCTOR], summary="Moderatsiyaga yuborish", request=None,
               responses={200: OnboardingStatusSerializer, 400: DETAIL})
class OnboardingSubmitView(APIView):
    permission_classes = [IsDoctor]

    def post(self, request):
        try:
            doctor = onboarding.submit(request.doctor)
        except onboarding.OnboardingError as exc:
            return _error(exc)
        return Response(OnboardingStatusSerializer(onboarding.get_status(doctor)).data)


@extend_schema(tags=[TAG_DOCTOR], summary="Onboarding holati va yetishmayotgan maydonlar",
               responses=OnboardingStatusSerializer)
class OnboardingStatusView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        return Response(OnboardingStatusSerializer(onboarding.get_status(request.doctor)).data)


@extend_schema(tags=[TAG_DOCTOR], summary="Hujjatni yuklab olish (imzolangan havola)", responses={200: None, 404: None},
               auth=[])
class DocumentDownloadView(APIView):
    """Token o'zi ruxsat: faqat hujjat egasi yoki platforma admini oladi, 5 daqiqa amal qiladi.

    Token URL'da bo'lgani uchun `permission_classes = []` — `<img src>` / brauzer
    to'g'ridan ocha olsin. Muddati qisqa, shuning uchun havola tarqalsa ham xavf kichik.
    """

    permission_classes = []
    authentication_classes = []

    def get(self, request, token):
        try:
            document_id = onboarding.unsign_document(token)
        except onboarding.OnboardingError:
            raise Http404
        doc = DoctorDocument.objects.filter(id=document_id).first()
        if doc is None:
            raise Http404
        response = FileResponse(doc.file.open("rb"), content_type=doc.content_type)
        response["Content-Disposition"] = f'inline; filename="{doc.kind}{_ext(doc.content_type)}"'
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


def _ext(content_type: str) -> str:
    return {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}.get(content_type, "")


# ---------------------------------------------------------------------------
# Moderatsiya (platforma admini)
# ---------------------------------------------------------------------------


@extend_schema(tags=[TAG_MODERATION], summary="Moderatsiya navbati (SLA bo'yicha)",
               parameters=[OpenApiParameter("status", str, enum=[s for s in Doctor.Status.values])],
               responses=ModerationDoctorSerializer(many=True))
class ModerationDoctorListView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        qs = (Doctor.objects.select_related("user").prefetch_related("specializations", "documents")
              .filter(status=request.query_params.get("status", Doctor.Status.PENDING))
              .order_by("submitted_at", "created_at"))[:200]
        return Response(ModerationDoctorSerializer(qs, many=True, context={"request": request}).data)


@extend_schema(tags=[TAG_MODERATION], summary="Shifokor arizasi (hujjatlar bilan)",
               responses={200: ModerationDoctorSerializer, 404: None})
class ModerationDoctorDetailView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request, doctor_id):
        doctor = Doctor.objects.filter(id=doctor_id).select_related("user").first()
        if doctor is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(ModerationDoctorSerializer(doctor, context={"request": request}).data)


class _ModerationActionView(APIView):
    permission_classes = [IsPlatformAdmin]
    needs_reason = False

    def perform(self, doctor_id, admin, reason):  # pragma: no cover
        raise NotImplementedError

    def post(self, request, doctor_id):
        if not Doctor.objects.filter(id=doctor_id).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        reason = ""
        if self.needs_reason:
            serializer = ReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            reason = serializer.validated_data["reason"]
        try:
            doctor = self.perform(doctor_id, request.user, reason)
        except onboarding.OnboardingError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(ModerationDoctorSerializer(doctor, context={"request": request}).data)


@extend_schema(tags=[TAG_MODERATION], summary="Tasdiqlash", request=None,
               responses={200: ModerationDoctorSerializer, 409: DETAIL})
class ModerationApproveView(_ModerationActionView):
    def perform(self, doctor_id, admin, reason):
        return onboarding.approve(doctor_id, admin)


@extend_schema(tags=[TAG_MODERATION], summary="Rad etish (sabab majburiy)", request=ReasonSerializer,
               responses={200: ModerationDoctorSerializer, 409: DETAIL})
class ModerationRejectView(_ModerationActionView):
    needs_reason = True

    def perform(self, doctor_id, admin, reason):
        return onboarding.reject(doctor_id, admin, reason)


@extend_schema(tags=[TAG_MODERATION], summary="To'xtatish (sabab majburiy)", request=ReasonSerializer,
               responses={200: ModerationDoctorSerializer, 409: DETAIL})
class ModerationSuspendView(_ModerationActionView):
    needs_reason = True

    def perform(self, doctor_id, admin, reason):
        return onboarding.suspend(doctor_id, admin, reason)


@extend_schema(tags=[TAG_MODERATION], summary="Qayta faollashtirish", request=None,
               responses={200: ModerationDoctorSerializer, 409: DETAIL})
class ModerationReinstateView(_ModerationActionView):
    def perform(self, doctor_id, admin, reason):
        return onboarding.reinstate(doctor_id, admin)
