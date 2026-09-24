"""
notifications/management/commands/run_notification_scheduler.py — C9

Vaqti kelgan eslatmalarni yuboradi. Har daqiqada ishlaydi (cron yoki
`--loop` bilan doimiy jarayon sifatida).

    python manage.py run_notification_scheduler
    python manage.py run_notification_scheduler --loop

Advisory lock (E10): bir necha nusxa ishga tushsa ham, bir vaqtda faqat
bittasi yuboradi — mijoz ikkita bir xil SMS olmaydi.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from api.locks import advisory_lock
from api.notifications.scheduler import run_due_notifications

INTERVAL_SECONDS = 60


def run_once() -> int:
    with advisory_lock("notification_scheduler") as acquired:
        if not acquired:
            return 0
        return run_due_notifications()


class Command(BaseCommand):
    help = "Vaqti kelgan eslatmalarni yuboradi (har daqiqa)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Cron o'rniga doimiy jarayon sifatida ishlash",
        )

    def handle(self, *args, **options):
        if not options["loop"]:
            self.stdout.write(f"Yuborildi: {run_once()}")
            return

        while True:
            # Uzoq yashaydigan jarayon: uzilgan ulanishni yangilaymiz
            close_old_connections()
            sent = run_once()
            if sent:
                self.stdout.write(f"Yuborildi: {sent}")
            time.sleep(INTERVAL_SECONDS)
