from api.account.services import (
    AuthError,
    InvalidCode,
    InvalidPhone,
    SmsUnavailable,
    TooManyRequests,
    issue_tokens,
    logout,
    logout_all,
    request_otp,
    verify_otp,
)
from api.account.serializers import RefreshTokenSerializer, SwitchRoleSerializer, TokenPairSerializer
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response

from api.account.serializers import VerifyOtpSerializer, RequestOtpSerializer
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from api.account.utils import _client_ip
from api.account.serializers import OtpRequestedSerializer, VerifyOtpResponseSerializer
from api.docs import DETAIL, TAG_AUTH


@extend_schema(
    tags=[TAG_AUTH],
    summary="SMS kod so'rash",
    description="Raqam tizimda bor-yo'qligidan qat'i nazar javob bir xil. Lokalda kod runserver terminaliga chiqadi.",
    request=RequestOtpSerializer,
    responses={200: OtpRequestedSerializer, 400: DETAIL, 429: DETAIL, 503: DETAIL},
    auth=[],
)
class RequestOtpView(APIView):
    """POST /api/v1/auth/otp/request

    DRF throttle qo'shildi — bu servis darajasidagi rate limit ustiga
    QO'SHIMCHA qatlam. Ikkalasi ham kerak: throttle IP bo'yicha tez ishlaydi,
    services.py dagi cheklov esa telefon bo'yicha va aniqroq.
    """

    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"  # settings: DEFAULT_THROTTLE_RATES = {"otp": "10/hour"}

    def post(self, request):
        serializer = RequestOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            request_otp(serializer.validated_data["phone"], ip_address=_client_ip(request))
        except InvalidPhone as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except TooManyRequests as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS
            )
        except SmsUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        # Raqam tizimda bor-yo'qligini OSHKOR QILMAYMIZ — javob har doim bir xil
        return Response({"detail": "Kod yuborildi"})


@extend_schema(
    tags=[TAG_AUTH],
    summary="Kodni tekshirish va token olish",
    description="Raqam yangi bo'lsa foydalanuvchi shu yerda yaratiladi. `tokens.access` ni **Authorize** ga kiriting.",
    request=VerifyOtpSerializer,
    responses={200: VerifyOtpResponseSerializer, 400: DETAIL, 403: DETAIL, 429: DETAIL},
    auth=[],
)
class VerifyOtpView(APIView):
    """POST /api/v1/auth/otp/verify"""

    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user, is_new = verify_otp(
                serializer.validated_data["phone"], serializer.validated_data["code"]
            )
        except (InvalidCode, InvalidPhone) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except TooManyRequests as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except AuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        from api import roles

        available = roles.available_roles(user)
        return Response(
            {
                "tokens": issue_tokens(user),
                # ⬇ refresh endpointi: POST /api/v1/accounts/auth/token/refresh
                "is_new_user": is_new,
                "user": {
                    "id": str(user.id),
                    "phone": user.phone,
                    "full_name": user.full_name,
                    "role": user.role,
                    # D1: bir nechta rol bo'lsa ilova tanlash ekranini ko'rsatadi
                    "available_roles": available,
                    "active_role": roles.default_active_role(user, available),
                },
            }
        )


@extend_schema(
    tags=[TAG_AUTH],
    summary="Faol rolni almashtirish (mijoz / shifokor / klinika admini)",
    request=SwitchRoleSerializer,
    responses={200: TokenPairSerializer, 400: DETAIL, 403: DETAIL},
)
class SwitchRoleView(APIView):
    """POST /api/v1/accounts/auth/switch-role — yangi token juftligi, eski refresh bekor."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SwitchRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from api import roles

        if serializer.validated_data["role"] not in roles.available_roles(request.user):
            # Tekshiruv logout'dan OLDIN: rad etilsa eski sessiya saqlanib qoladi
            return Response({"detail": "Bu rol sizga berilmagan"}, status=status.HTTP_403_FORBIDDEN)
        try:
            logout(request.user, serializer.validated_data["refresh"])
            tokens = issue_tokens(request.user, active_role=serializer.validated_data["role"])
        except InvalidCode as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except AuthError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(tokens)


@extend_schema(
    tags=[TAG_AUTH],
    summary="Chiqish (joriy qurilma)",
    description="Refresh token qora ro'yxatga tushadi va qayta ishlatib bo'lmaydi.",
    request=RefreshTokenSerializer,
    responses={204: None, 400: DETAIL},
)
class LogoutView(APIView):
    """POST /api/v1/accounts/auth/logout"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            logout(request.user, serializer.validated_data["refresh"])
        except InvalidCode as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=[TAG_AUTH],
    summary="Barcha qurilmalardan chiqish",
    request=None,
    responses={204: None},
)
class LogoutAllView(APIView):
    """POST /api/v1/accounts/auth/logout/all"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout_all(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=[TAG_AUTH],
    summary="Akkauntni o'chirish (anonimlashtirish)",
    description="Shaxsiy ma'lumotlar o'chiriladi, bron va to'lov yozuvlari anonim holda qonuniy muddatgacha qoladi.",
    request=None,
    responses={204: None, 409: DETAIL},
)
class DeleteAccountView(APIView):
    """POST /api/v1/accounts/me/delete (A10)"""

    permission_classes = [IsAuthenticated]
    write_throttle_scope = "sensitive"

    def post(self, request):
        from api.account.deletion import DeletionBlocked, delete_account

        try:
            delete_account(request.user)
        except DeletionBlocked as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)
