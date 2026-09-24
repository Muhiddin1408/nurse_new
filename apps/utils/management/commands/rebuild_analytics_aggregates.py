"""E14: kunlik analitik agregatni qayta hisoblaydi. Kuniga bir marta (cron).

Idempotent — istalgan payt qayta ishlatish mumkin (`analytics/aggregates.py`).
"""

from django.core.management.base import BaseCommand

from analytics.aggregates import DEFAULT_DAYS, rebuild


class Command(BaseCommand):
    help = "daily_booking_agg ni oxirgi N kun uchun qayta hisoblaydi (E14)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=DEFAULT_DAYS,
            help="Nechta oxirgi kun qayta hisoblansin (backfill uchun kattaroq bering)",
        )

    def handle(self, *args, **options):
        rows = rebuild(days=options["days"])
        self.stdout.write(f"daily_booking_agg: {rows} qator ({options['days']} kun)")
