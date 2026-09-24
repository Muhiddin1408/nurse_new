"""
doctor/earnings_views.py — daromad paneli, payout, to'lov rekvizitlari (D6), Telegram (D11).

    GET  /api/v1/doctor/earnings/summary?period=today|week|month  (yoki from/to)
    GET  /api/v1/doctor/earnings/transactions?from=&to=
    GET  /api/v1/doctor/payouts
    GET  /api/v1/doctor/payouts/{id}/statement           (CSV, Excel ochadi)
    GET/PUT /api/v1/doctor/payout-account
    GET  /api/v1/doctor/telegram/link

    GET  /api/v1/moderation/payouts?status=pending
    POST /api/v1/moderation/payouts/{id}/mark-paid
    POST /api/v1/telegram/webhook                          (Telegram chaqiradi)
"""

import hmac

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.docs import DETAIL, TAG_DOCTOR_EARNINGS, TAG_MODERATION
from api.doctor import earnings
from api.permissions import IsApprovedDoctor, IsDoctor, IsPlatformAdmin
from apps.payment.models import PayoutAccount, PayoutPeriod


def _money(v):
    return str(v)


class PayoutSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    doctor_id = serializers.UUIDField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    status = serializers.CharField()
    bookings_count = serializers.IntegerField()
    gross_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    platform_fee = serializers.DecimalField(max_digits=14, decimal_places=2)
    provider_fee = serializers.DecimalField(max_digits=14, decimal_places=2)
    refunds_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    net_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    paid_at = serializers.DateTimeField(allow_null=True)
    bank_reference = serializers.CharField()


class PayoutAccountSerializer(serializers.Serializer):
    holder_name = serializers.CharField(max_length=255)
    bank_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    account_number = serializers.RegexField(r"^\d{16}$|^\d{20}$", write_only=True,
                                            error_messages={"invalid": "Karta (16) yoki hisob raqami (20 raqam)"})
    masked_number = serializers.CharField(read_only=True)
    mfo = serializers.RegexField(r"^\d{5}$", required=False, allow_blank=True)
    inn = serializers.RegexField(r"^\d{9}(\d{5})?$", required=False, allow_blank=True)


def _summary_dict(s: earnings.Summary, with_lines: bool) -> dict:
    data = {
        "period_from": s.period_from.isoformat(), "period_to": s.period_to.isoformat(),
        "bookings_count": s.bookings_count, "gross": _money(s.gross), "platform_fee": _money(s.platform_fee),
        "provider_fee": _money(s.provider_fee), "refunds": _money(s.refunds), "net": _money(s.net),
        "paid_out": _money(s.paid_out), "awaiting_payout": _money(s.awaiting_payout),
    }
    if with_lines:
        data["transactions"] = [{
            "booking_id": str(l.booking_id), "number": l.number, "date": l.date.isoformat(), "patient": l.patient,
            "services": l.services, "status": l.status, "gross": _money(l.gross),
            "platform_fee": _money(l.platform_fee), "provider_fee": _money(l.provider_fee),
            "refunds": _money(l.refunds), "net": _money(l.net),
            "payout_id": str(l.payout_id) if l.payout_id else None, "payout_status": l.payout_status,
        } for l in s.lines]
    return data


def _dates(request):
    f = serializers.DateField()
    raw_from, raw_to = request.query_params.get("from"), request.query_params.get("to")
    try:
        return (f.to_internal_value(raw_from) if raw_from else None,
                f.to_internal_value(raw_to) if raw_to else None)
    except serializers.ValidationError:
        raise earnings.EarningsError("from/to: YYYY-MM-DD")


@extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="Daromad xulosasi",
               parameters=[OpenApiParameter("period", OpenApiTypes.STR, enum=["today", "week", "month"]),
                           OpenApiParameter("from", OpenApiTypes.DATE), OpenApiParameter("to", OpenApiTypes.DATE)],
               responses={200: OpenApiTypes.OBJECT, 400: DETAIL})
class EarningsSummaryView(APIView):
    permission_classes = [IsDoctor]  # to'xtatilgan shifokor ham o'z pulini ko'ra olishi kerak

    def get(self, request):
        try:
            frm, to = _dates(request)
            s = earnings.summary(request.doctor, period=request.query_params.get("period"), date_from=frm, date_to=to)
        except earnings.EarningsError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_summary_dict(s, with_lines=False))


@extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="Har bir bron bo'yicha daromad satrlari",
               parameters=[OpenApiParameter("from", OpenApiTypes.DATE, required=True),
                           OpenApiParameter("to", OpenApiTypes.DATE, required=True)],
               responses={200: OpenApiTypes.OBJECT, 400: DETAIL})
class EarningsTransactionsView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        try:
            frm, to = _dates(request)
            s = earnings.summary(request.doctor, date_from=frm, date_to=to, with_lines=True)
        except earnings.EarningsError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_summary_dict(s, with_lines=True))


@extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="To'lov davrlari", responses=PayoutSerializer(many=True))
class DoctorPayoutListView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request):
        qs = PayoutPeriod.objects.filter(doctor=request.doctor).order_by("-period_end")[:104]
        return Response(PayoutSerializer(qs, many=True).data)


@extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="To'lov hisoboti (CSV / Excel)", responses={(200, "text/csv"): OpenApiTypes.STR})
class DoctorPayoutStatementView(APIView):
    permission_classes = [IsDoctor]

    def get(self, request, payout_id):
        payout = PayoutPeriod.objects.filter(id=payout_id, doctor=request.doctor).select_related("doctor__user").first()
        if payout is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        resp = HttpResponse(earnings.statement_csv(payout), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="medbron_payout_{payout.period_start}_{payout.period_end}.csv"'
        return resp


class PayoutAccountView(APIView):
    permission_classes = [IsDoctor]

    @extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="To'lov rekvizitlari", responses={200: PayoutAccountSerializer, 404: None})
    def get(self, request):
        acc = PayoutAccount.objects.filter(doctor=request.doctor).first()
        if acc is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(PayoutAccountSerializer(acc).data)

    @extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="To'lov rekvizitlarini saqlash", request=PayoutAccountSerializer,
                   responses={200: PayoutAccountSerializer})
    def put(self, request):
        s = PayoutAccountSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        acc, _ = PayoutAccount.objects.update_or_create(doctor=request.doctor, defaults=s.validated_data)
        return Response(PayoutAccountSerializer(acc).data)


@extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="Telegram botni ulash havolasi (15 daqiqa)",
               responses={200: OpenApiTypes.OBJECT})
class TelegramLinkView(APIView):
    permission_classes = [IsApprovedDoctor]

    def get(self, request):
        from api.telegram.bot import LINK_TTL_SECONDS, make_link

        return Response({"url": make_link(request.doctor), "expires_in": LINK_TTL_SECONDS,
                         "linked": request.doctor.telegram_chat_id is not None})


# --- admin ------------------------------------------------------------------


class MarkPaidSerializer(serializers.Serializer):
    bank_reference = serializers.CharField(max_length=255)


@extend_schema(tags=[TAG_MODERATION], summary="Payout navbati", responses=PayoutSerializer(many=True),
               parameters=[OpenApiParameter("status", OpenApiTypes.STR, enum=["pending", "paid"])])
class AdminPayoutListView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        qs = PayoutPeriod.objects.filter(status=request.query_params.get("status", "pending")).order_by("period_end")
        return Response(PayoutSerializer(qs[:500], many=True).data)


@extend_schema(tags=[TAG_MODERATION], summary="Payout to'landi deb belgilash (bank hujjati raqami bilan)",
               request=MarkPaidSerializer, responses={200: PayoutSerializer, 400: DETAIL, 404: None})
class AdminPayoutMarkPaidView(APIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request, payout_id):
        s = MarkPaidSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if not PayoutPeriod.objects.filter(id=payout_id).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not PayoutAccount.objects.filter(doctor__payouts__id=payout_id).exists():
            return Response({"detail": "Shifokorning to'lov rekvizitlari kiritilmagan"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payout = earnings.mark_paid(payout_id, admin=request.user, bank_reference=s.validated_data["bank_reference"])
        except earnings.EarningsError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(PayoutSerializer(payout).data)


@extend_schema(tags=[TAG_DOCTOR_EARNINGS], summary="Telegram webhook (faqat Telegram chaqiradi)",
               request=OpenApiTypes.OBJECT, responses={200: None, 403: None}, auth=[])
class TelegramWebhookView(APIView):
    permission_classes = []
    authentication_classes = []
    # A7: provayder qayta urinishini rad etmaymiz — himoya IP allowlist + imzo
    throttle_classes = []

    def post(self, request):
        from api.telegram.bot import handle_update

        expected = settings.TELEGRAM_WEBHOOK_SECRET
        got = request.META.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN", "")
        # fail-closed: sekret sozlanmagan bo'lsa ham rad etiladi
        if not expected or not hmac.compare_digest(got, expected):
            return Response(status=status.HTTP_403_FORBIDDEN)
        if isinstance(request.data, dict):
            handle_update(request.data)
        # Telegram 200 olmasa qayta yuboradi — ichki xato bo'lsa ham 200
        return Response(status=status.HTTP_200_OK)
