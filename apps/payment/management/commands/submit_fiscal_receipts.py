"""B12: Click to'lovlari uchun fiskal chek elementlarini yuborish (har 5 daqiqa).

Payme chekni `CheckPerformTransaction.detail` dan o'zi chiqaradi — u bu yerda yo'q.
Click'da chek to'lovdan KEYIN alohida so'rov bilan yuboriladi; yuborilmagani
`provider_state.fiscal` da `submitted` bo'lmaydi va keyingi ishga tushishda qayta urinadi.
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.payments.providers import ManualRefundRequired, ProviderError, get_provider
from apps.payment.models import Payment

logger = logging.getLogger(__name__)


def submit_pending(limit: int = 200) -> tuple[int, int]:
    ok = failed = 0
    since = timezone.now() - timedelta(days=3)
    qs = Payment.objects.filter(
        provider=Payment.Provider.CLICK, status=Payment.Status.SUCCEEDED, updated_at__gte=since
    ).order_by("updated_at")[:limit]
    provider = get_provider(Payment.Provider.CLICK)
    for payment in qs:
        state = dict(payment.provider_state or {})
        if (state.get("fiscal") or {}).get("submitted"):
            continue
        try:
            response = provider.submit_fiscal(payment)
        except (ProviderError, ManualRefundRequired) as exc:
            failed += 1
            logger.warning("Fiskal chek yuborilmadi: payment=%s (%s)", payment.id, exc)
            continue
        submitted = int(response.get("error_code", -1)) == 0
        state["fiscal"] = {"submitted": submitted, "response": response, "at": timezone.now().isoformat()}
        Payment.objects.filter(id=payment.id).update(provider_state=state)
        ok += int(submitted)
        failed += int(not submitted)
    return ok, failed


class Command(BaseCommand):
    help = "Click to'lovlari uchun fiskal cheklarni yuboradi (B12)"

    def handle(self, *args, **options):
        ok, failed = submit_pending()
        self.stdout.write(f"yuborildi: {ok}, xato: {failed}")
