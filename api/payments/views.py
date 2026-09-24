import json
import time

from django.conf import settings
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.account.utils import _client_ip
from api.docs import DETAIL, TAG_PAYMENT
from api.payments.auth_webhook import verify_payme_signature
from api.payments.bron import CheckoutNotAllowed, create_checkout
from api.payments.services import PaymentError
from api.payments.webhook import handle_payme_webhook
from apps.booking.models import Booking
from apps.payment.models import PaymeCallbackLog, Payment


class CheckoutRequestSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=[Payment.Provider.PAYME, Payment.Provider.CLICK])


class CheckoutResponseSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField()
    checkout_url = serializers.URLField()
    amount = serializers.IntegerField()
    expires_at = serializers.DateTimeField(allow_null=True)


@extend_schema(
    tags=[TAG_PAYMENT],
    summary="To'lovni boshlash (checkout)",
    description=(
        "Bron `pending_payment` holatida bo'lishi shart. Idempotent: shu provayder uchun "
        "ochiq to'lov bo'lsa, o'sha qaytariladi."
    ),
    request=CheckoutRequestSerializer,
    responses={200: CheckoutResponseSerializer, 404: None, 409: DETAIL},
)
class CheckoutView(APIView):
    """POST /api/v1/payment/bookings/{booking_id}/checkout"""

    permission_classes = [IsAuthenticated]

    def post(self, request, booking_id):
        serializer = CheckoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        booking = Booking.objects.filter(id=booking_id, client=request.user).first()
        if booking is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            checkout = create_checkout(
                booking=booking, provider=serializer.validated_data["provider"]
            )
        except CheckoutNotAllowed as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

        return Response(CheckoutResponseSerializer(checkout.__dict__).data)


def _payme_error(code: int, message: str) -> dict:
    return {"error": {"code": code, "message": {"uz": message, "ru": message, "en": message}}}


@extend_schema(
    tags=[TAG_PAYMENT],
    summary="Payme JSON-RPC webhook (faqat Payme chaqiradi)",
    description=(
        "Basic Auth: `Paycom:<PAYME_SECRET_KEY>`. Metodlar: CheckPerformTransaction, "
        "CreateTransaction, PerformTransaction, CancelTransaction, CheckTransaction, "
        "GetStatement, SetFiscalData. Xatoda ham HTTP 200, xato `error` maydonida."
    ),
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT},
    auth=[],
)
class PaymeWebhookView(APIView):
    """POST /api/v1/payment/payme/webhook

    Himoya qatlamlari (A8):
        1. IP allowlist (`PAYME_ALLOWED_IPS`, bo'sh bo'lsa o'chiq)
        2. Basic Auth + compare_digest, fail-closed
        3. Tana hajmi cheklovi
        4. Har bir chaqiruv `PaymeCallbackLog` ga yoziladi
    """

    permission_classes = []
    authentication_classes = []
    # A7: provayder qayta urinishini rad etmaymiz — himoya IP allowlist + imzo
    throttle_classes = []

    def post(self, request):
        started = time.monotonic()
        ip = _client_ip(request)
        body: dict = {}
        result = self._dispatch(request, ip, body)

        response_body = {"jsonrpc": "2.0", "id": body.get("id"), **result}
        PaymeCallbackLog.objects.create(
            method=str(body.get("method", ""))[:64],
            params=body.get("params") if isinstance(body.get("params"), dict) else {},
            response=result,
            ip=ip,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        # ⬇ Payme protokoli hatto xatoda ham HTTP 200 kutadi
        return Response(response_body, status=200)

    def _dispatch(self, request, ip, body: dict) -> dict:
        allowed = settings.PAYME_ALLOWED_IPS
        if allowed and ip not in allowed:
            return _payme_error(-32504, "Ruxsat yo'q")

        if len(request.body) > settings.PAYME_MAX_BODY_BYTES:
            return _payme_error(-32600, "So'rov juda katta")

        if not verify_payme_signature(request):
            return _payme_error(-32504, "Ruxsat yo'q")

        try:
            parsed = json.loads(request.body or b"{}")
        except ValueError:
            return _payme_error(-32700, "JSON xato")
        if not isinstance(parsed, dict):
            return _payme_error(-32600, "So'rov noto'g'ri")
        body.update(parsed)

        if "method" not in body or "params" not in body:
            return _payme_error(-32600, "So'rov noto'g'ri")

        try:
            return handle_payme_webhook(method=body["method"], params=body["params"])
        except PaymentError as exc:
            return _payme_error(-31008, str(exc))


@extend_schema(
    tags=[TAG_PAYMENT],
    summary="Click SHOP API webhook (faqat Click chaqiradi)",
    description="`prepare` (action=0) va `complete` (action=1). MD5 `sign_string`. Xatoda ham HTTP 200, xato `error` maydonida.",
    request=OpenApiTypes.OBJECT,
    responses={200: OpenApiTypes.OBJECT},
    auth=[],
)
class ClickWebhookView(APIView):
    """POST /api/v1/payment/click/prepare | /complete (B6)"""

    permission_classes = []
    authentication_classes = []
    throttle_classes = []  # A7: provayder qayta urinishini rad etmaymiz
    action: int = 0

    def post(self, request):
        from api.payments import click
        from apps.payment.models import ProviderRequestLog

        started = time.monotonic()
        ip = _client_ip(request)
        params: dict = {}
        if settings.CLICK_ALLOWED_IPS and ip not in settings.CLICK_ALLOWED_IPS:
            result = {"error": click.ERR_SIGN, "error_note": "Ruxsat yo'q"}
        # Hajm tanani O'QISHDAN OLDIN, sarlavhadan
        elif int(request.META.get("CONTENT_LENGTH") or 0) > settings.PAYME_MAX_BODY_BYTES:
            result = {"error": click.ERR_BAD_REQUEST, "error_note": "So'rov juda katta"}
        else:
            params = {k: v for k, v in request.data.items()} if hasattr(request.data, "items") else {}
            result = click.handle(self.action, params)
        # Nizoda yagona dalil — kiruvchi so'rov va javob (imzo logga yozilmaydi)
        ProviderRequestLog.objects.create(
            provider="click",
            operation=f"webhook_{'prepare' if self.action == 0 else 'complete'}",
            request={k: v for k, v in params.items() if k != "sign_string"} | {"ip": ip},
            response=result,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return Response(result, status=200)


class ClickPrepareView(ClickWebhookView):
    action = 0


class ClickCompleteView(ClickWebhookView):
    action = 1
