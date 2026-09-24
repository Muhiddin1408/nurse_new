"""
account/preferences.py — til va bildirishnoma sozlamalari (C13, C14).

    GET/PATCH /api/v1/accounts/me/preferences
    POST      /api/v1/accounts/me/push-devices        {token, platform}
    DELETE    /api/v1/accounts/me/push-devices        {token}
    GET       /api/v1/accounts/me/telegram/link
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api import audit
from api.docs import TAG_AUTH
from apps.notifications.models import NotificationPreference, PushDevice


class PreferencesSerializer(serializers.Serializer):
    language = serializers.ChoiceField(choices=["uz", "ru"], required=False)
    push_enabled = serializers.BooleanField(required=False)
    telegram_enabled = serializers.BooleanField(required=False)
    sms_enabled = serializers.BooleanField(required=False)
    marketing_opt_in = serializers.BooleanField(required=False)
    telegram_linked = serializers.BooleanField(read_only=True)


def _data(user, pref: NotificationPreference) -> dict:
    return {
        "language": user.preferred_language,
        "push_enabled": pref.push_enabled,
        "telegram_enabled": pref.telegram_enabled,
        "sms_enabled": pref.sms_enabled,
        "marketing_opt_in": pref.marketing_opt_in,
        "telegram_linked": pref.telegram_chat_id is not None,
    }


class PreferencesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=[TAG_AUTH], summary="Til va bildirishnoma sozlamalari", responses=PreferencesSerializer)
    def get(self, request):
        pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return Response(_data(request.user, pref))

    @extend_schema(tags=[TAG_AUTH], summary="Sozlamalarni o'zgartirish",
                   description="Tranzaksion xabarlar (bron, to'lov) baribir yetkaziladi: boshqa kanal ishlamasa "
                               "SMS o'chirilgan bo'lsa ham ketadi. Marketing — faqat `marketing_opt_in` bilan.",
                   request=PreferencesSerializer, responses=PreferencesSerializer)
    def patch(self, request):
        s = PreferencesSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        data = dict(s.validated_data)
        pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
        before = _data(request.user, pref)
        if "language" in data:
            request.user.preferred_language = data.pop("language")
            request.user.save(update_fields=["preferred_language", "updated_at"])
        for k, v in data.items():
            setattr(pref, k, v)
        pref.save()
        after = _data(request.user, pref)
        if before["marketing_opt_in"] != after["marketing_opt_in"]:
            # Rozilik — huquqiy ahamiyatga ega, qachon berilgani/qaytarilgani iz qoldiradi
            audit.record("user.marketing_consent", obj=request.user, before={"opt_in": before["marketing_opt_in"]},
                         after={"opt_in": after["marketing_opt_in"]})
        return Response(after)


class PushDeviceSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512)
    platform = serializers.ChoiceField(choices=PushDevice.Platform.choices, required=False)


class PushDevicesView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=[TAG_AUTH], summary="Push qurilmani ro'yxatdan o'tkazish (har ilova ochilganda)",
                   request=PushDeviceSerializer, responses={204: None})
    def post(self, request):
        s = PushDeviceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "platform" not in s.validated_data:
            return Response({"platform": ["Majburiy"]}, status=status.HTTP_400_BAD_REQUEST)
        # Token boshqa akkauntda bo'lsa (bitta telefonda akkaunt almashtirildi) — shu akkauntga o'tadi
        PushDevice.objects.update_or_create(
            token=s.validated_data["token"],
            defaults={"user": request.user, "platform": s.validated_data["platform"], "is_active": True},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(tags=[TAG_AUTH], summary="Push qurilmani o'chirish (logout'da)", request=PushDeviceSerializer,
                   responses={204: None})
    def delete(self, request):
        s = PushDeviceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        PushDevice.objects.filter(user=request.user, token=s.validated_data["token"]).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ClientTelegramLinkView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=[TAG_AUTH], summary="Telegram'ni ulash havolasi (SMS o'rniga bepul kanal)",
                   responses=OpenApiTypes.OBJECT)
    def get(self, request):
        from api.telegram.bot import LINK_TTL_SECONDS, make_client_link

        pref = NotificationPreference.objects.filter(user=request.user).first()
        return Response({"url": make_client_link(request.user), "expires_in": LINK_TTL_SECONDS,
                         "linked": bool(pref and pref.telegram_chat_id)})
