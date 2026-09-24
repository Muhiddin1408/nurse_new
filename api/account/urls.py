from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from api.account import preferences

from api.account.views import (
    DeleteAccountView,
    LogoutAllView,
    LogoutView,
    RequestOtpView,
    SwitchRoleView,
    VerifyOtpView,
)

urlpatterns = [
    path("auth/otp/request", RequestOtpView.as_view(), name="otp-request"),
    path("auth/otp/verify", VerifyOtpView.as_view(), name="otp-verify"),
    # Rotation + blacklist settings.SIMPLE_JWT da yoqilgan
    path("auth/token/refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/logout", LogoutView.as_view(), name="logout"),
    path("auth/logout/all", LogoutAllView.as_view(), name="logout-all"),
    path("auth/switch-role", SwitchRoleView.as_view(), name="switch-role"),
    path("me/delete", DeleteAccountView.as_view(), name="account-delete"),
    path("me/preferences", preferences.PreferencesView.as_view(), name="account-preferences"),
    path("me/push-devices", preferences.PushDevicesView.as_view(), name="account-push-devices"),
    path("me/telegram/link", preferences.ClientTelegramLinkView.as_view(), name="account-telegram-link"),
]
