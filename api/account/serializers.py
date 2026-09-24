from rest_framework import serializers


class RequestOtpSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)


class VerifyOtpSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=20)
    code = serializers.CharField(min_length=6, max_length=6)


class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class SwitchRoleSerializer(serializers.Serializer):
    refresh = serializers.CharField()
    role = serializers.ChoiceField(choices=["client", "doctor", "clinic_admin", "platform_admin"])


# --- Faqat Swagger hujjati uchun (javob shakli) ---


class OtpRequestedSerializer(serializers.Serializer):
    detail = serializers.CharField(default="Kod yuborildi")


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class AuthUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    phone = serializers.CharField()
    full_name = serializers.CharField()
    role = serializers.CharField()


class VerifyOtpResponseSerializer(serializers.Serializer):
    tokens = TokenPairSerializer()
    is_new_user = serializers.BooleanField()
    user = AuthUserSerializer()
