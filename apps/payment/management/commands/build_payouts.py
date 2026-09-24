"""
payment/management/commands/build_payouts.py — haftalik ish (dushanba 06:00)

    python manage.py build_payouts                       # o'tgan to'liq hafta
    python manage.py build_payouts --end 2026-09-13

Idempotent: bron `PayoutLine` da UNIQUE — qayta ishga tushsa ham ikki marta kiritilmaydi.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from api.doctor.earnings import build_payouts, last_full_week
from api.locks import advisory_lock


class Command(BaseCommand):
    help = "Shifokorlar uchun haftalik payout'larni hisoblaydi"

    def add_arguments(self, parser):
        parser.add_argument("--end", type=date.fromisoformat, help="Davr oxiri (yakshanba)")

    def handle(self, *args, **options):
        if options["end"]:
            end = options["end"]
            start = end - timedelta(days=6)
        else:
            start, end = last_full_week()
        with advisory_lock("build_payouts") as acquired:
            if not acquired:
                return
            created = build_payouts(start, end)
        self.stdout.write(f"{start} — {end}: {len(created)} ta payout")
