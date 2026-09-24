"""
booking/review_views.py — sharh endpointlari (C11). Mantiq `reviews.py` da.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.booking import reviews
from api.catalog.utils import _int_param
from api.docs import DETAIL, TAG_BOOKING, TAG_CATALOG, TAG_DOCTOR_WORK, TAG_MODERATION
from api.permissions import IsApprovedDoctor, IsPlatformAdmin

_PAGINATION = [
    OpenApiParameter("limit", OpenApiTypes.INT, description="Default 20, max 50"),
    OpenApiParameter("offset", OpenApiTypes.INT, description="Default 0"),
]


def _page(request):
    return {
        "limit": _int_param(request, "limit", default=20, max_value=50),
        "offset": _int_param(request, "offset", default=0),
    }


def _error(exc: reviews.ReviewError):
    if isinstance(exc, reviews.ReviewNotFound):
        return Response(status=status.HTTP_404_NOT_FOUND)
    return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)


class CreateReviewSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=reviews.MAX_COMMENT_LENGTH, default="")
    is_anonymous = serializers.BooleanField(required=False, default=False)


class ReplySerializer(serializers.Serializer):
    text = serializers.CharField(max_length=reviews.MAX_COMMENT_LENGTH)


@extend_schema(
    tags=[TAG_BOOKING],
    summary="Qabulga sharh yozish",
    description="Faqat `completed` bronga, 30 kun ichida, bitta marta. Shubhali matn moderatsiyaga tushadi.",
    request=CreateReviewSerializer,
    responses={201: OpenApiTypes.OBJECT, 404: None, 409: DETAIL},
)
class CreateReviewView(APIView):
    """POST /api/v1/booking/bookings/{id}/review"""

    permission_classes = [IsAuthenticated]

    def post(self, request, booking_id):
        s = CreateReviewSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            review = reviews.create_review(
                client_id=request.user.id, booking_id=booking_id, data=reviews.ReviewInput(**s.validated_data)
            )
        except reviews.ReviewError as exc:
            return _error(exc)
        return Response(reviews.serialize(review, public=False), status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG_CATALOG], summary="Shifokor sharhlari", parameters=_PAGINATION,
               responses=OpenApiTypes.OBJECT, auth=[])
class DoctorReviewsView(APIView):
    """GET /api/v1/catalog/doctors/{id}/reviews — "Отзывы" tab'i."""

    permission_classes = []

    def get(self, request, doctor_id):
        rows = reviews.doctor_reviews(doctor_id, **_page(request))
        return Response([reviews.serialize(r) for r in rows])


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Mening sharhlarim", parameters=_PAGINATION,
               responses=OpenApiTypes.OBJECT)
class MyDoctorReviewsView(APIView):
    """GET /api/v1/doctor/reviews"""

    permission_classes = [IsApprovedDoctor]

    def get(self, request):
        rows = reviews.doctor_reviews(request.doctor.id, **_page(request))
        return Response([reviews.serialize(r) for r in rows])


@extend_schema(tags=[TAG_DOCTOR_WORK], summary="Sharhga javob (bir marta)", request=ReplySerializer,
               responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
class ReplyReviewView(APIView):
    """POST /api/v1/doctor/reviews/{id}/reply"""

    permission_classes = [IsApprovedDoctor]

    def post(self, request, review_id):
        s = ReplySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            review = reviews.reply(doctor_id=request.doctor.id, review_id=review_id, text=s.validated_data["text"])
        except reviews.ReviewError as exc:
            return _error(exc)
        return Response(reviews.serialize(review))


@extend_schema(tags=[TAG_MODERATION], summary="Moderatsiyadagi sharhlar", parameters=_PAGINATION,
               responses=OpenApiTypes.OBJECT)
class ModerationReviewListView(APIView):
    """GET /api/v1/moderation/reviews"""

    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        rows = reviews.pending_reviews(**_page(request))
        return Response([reviews.serialize(r, public=False) for r in rows])


class _ModerateView(APIView):
    permission_classes = [IsPlatformAdmin]
    approve = True

    def post(self, request, review_id):
        try:
            review = reviews.moderate(review_id=review_id, approve=self.approve, admin_id=request.user.id)
        except reviews.ReviewError as exc:
            return _error(exc)
        return Response(reviews.serialize(review, public=False))


@extend_schema(tags=[TAG_MODERATION], summary="Sharhni e'lon qilish", request=None,
               responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
class ModerationReviewApproveView(_ModerateView):
    approve = True


@extend_schema(tags=[TAG_MODERATION], summary="Sharhni rad etish", request=None,
               responses={200: OpenApiTypes.OBJECT, 404: None, 409: DETAIL})
class ModerationReviewRejectView(_ModerateView):
    approve = False
