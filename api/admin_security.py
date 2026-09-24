"""
admin_security.py — Django admin himoyasi (A12).

    1. Tasodifiy URL prefiksi: `ADMIN_URL` (prodda majburiy, `admin/` emas) —
       skanerlar `/admin/` ni topmaydi.
    2. IP allowlist: `ADMIN_ALLOWED_IPS` (ofis / VPN). Boshqa IP — 404, 403 EMAS:
       panel mavjudligi ham oshkor qilinmaydi.
    3. Majburiy TOTP 2FA (`ADMIN_2FA_REQUIRED`, prodda yoqiq).
    4. Sessiya 30 daqiqa, Secure + SameSite=Strict cookie (settings).
    5. Har bir admin amali audit log'da (apps/utils/signals.py, A11).
"""

from __future__ import annotations

import ipaddress

from django import forms
from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.http import Http404

from api import totp


class TOTPAdminAuthenticationForm(AdminAuthenticationForm):
    otp_token = forms.CharField(required=False, max_length=6, label="2FA kodi")

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        device = getattr(user, "totp_device", None)
        if device is None:
            if settings.ADMIN_2FA_REQUIRED:
                raise forms.ValidationError(
                    "2FA sozlanmagan. Administrator `manage.py admin_2fa_setup` bilan sozlasin.",
                    code="2fa_missing",
                )
            return
        step = totp.verify(device.secret, self.cleaned_data.get("otp_token", ""), last_step=device.last_step)
        if step is None:
            raise forms.ValidationError("2FA kodi noto'g'ri yoki eskirgan", code="2fa_invalid")
        type(device).objects.filter(pk=device.pk).update(last_step=step)


def _ip_allowed(ip: str | None) -> bool:
    allowed = settings.ADMIN_ALLOWED_IPS
    if not allowed:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(net, strict=False) for net in allowed)


class AdminIPRestrictionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.prefix = "/" + settings.ADMIN_URL.strip("/") + "/"

    def __call__(self, request):
        if request.path.startswith(self.prefix):
            from api.account.utils import _client_ip

            if not _ip_allowed(_client_ip(request)):
                raise Http404
        return self.get_response(request)
