"""
audit.py — o'zgarmas audit jurnali (A11).

"Kim bu bronni qo'lda yakunladi?", "Kim narxni 3 barobar oshirdi?" — javob shu yerda.

    audit.record("doctor.approve", obj=doctor, before={...}, after={...})

Kim va qayerdan (actor, IP) — `AuditContextMiddleware` so'rovni kontekstga
qo'yadi, `record` uni o'zi o'qiydi. Servis funksiyalariga `request` uzatish
shart emas. So'rovsiz (cron, consumer) chaqirilsa actor = "system".

`record` chaqiruvchining tranzaksiyasi ICHIDA yoziladi: amal bekor bo'lsa
audit yozuvi ham qolmaydi (bo'lmagan amal jurnalda ko'rinmasin).
"""

from __future__ import annotations

import contextvars
import json
import logging

from django.core.serializers.json import DjangoJSONEncoder

from api.observability.correlation import get_correlation_id

logger = logging.getLogger(__name__)

_current_request: contextvars.ContextVar = contextvars.ContextVar("audit_request", default=None)


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)


def _jsonable(value):
    if value is None:
        return None
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _actor():
    """(user | None, role, ip). DRF foydalanuvchini `request._request.user` ga ham
    yozadi, shuning uchun JWT bilan kirgan foydalanuvchi ham shu yerda ko'rinadi."""
    request = _current_request.get()
    if request is None:
        return None, "system", None
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        user, role = None, "anonymous"
    else:
        role = "superuser" if user.is_superuser else (user.role or "")
    from api.account.utils import _client_ip

    return user, role, _client_ip(request)


def record(action: str, *, obj=None, object_type: str = "", object_id="", before=None, after=None,
           actor=None) -> None:
    from apps.utils.models import AuditLog

    ctx_user, role, ip = _actor()
    user = actor if actor is not None else ctx_user
    if actor is not None:
        role = "superuser" if actor.is_superuser else (actor.role or "")
    AuditLog.objects.create(
        actor=user,
        actor_role=role,
        action=action,
        object_type=object_type or (obj._meta.label_lower if obj is not None else ""),
        object_id=str(object_id or (obj.pk if obj is not None else "")),
        before=_jsonable(before),
        after=_jsonable(after),
        ip=ip,
        correlation_id=get_correlation_id() or "",
    )
