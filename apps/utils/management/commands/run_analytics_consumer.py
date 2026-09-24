"""
    python manage.py run_analytics_consumer [--init-schema]

`booking.events` -> ClickHouse `fact_booking` (partiyalab). Consumer group: `analytics-loader`.

`analytics` Django app emas, shuning uchun buyruq `apps/utils` da turadi.
"""

from django.core.management.base import BaseCommand

from analytics.consumer import ensure_schema, run_analytics_consumer
from api.events.runner import GracefulStop


class Command(BaseCommand):
    help = "Bron hodisalarini ClickHouse'ga yuklaydi (Kafka consumer)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--init-schema",
            action="store_true",
            help="Avval ClickHouse jadvallarini yaratish (IF NOT EXISTS — xavfsiz)",
        )

    def handle(self, *args, **options):
        if options["init_schema"]:
            ensure_schema()
            self.stdout.write("ClickHouse sxemasi tayyor")
        run_analytics_consumer(stop=GracefulStop())
        self.stdout.write("Analytics consumer to'xtadi")
