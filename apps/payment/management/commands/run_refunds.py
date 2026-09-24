"""
payment/management/commands/run_refunds.py — davriy ish (har daqiqa)

Navbatdagi refund'larni provayderga yuboradi (B4).

    python manage.py run_refunds
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from api.locks import advisory_lock
from api.payments.refunds import process_due_refunds


def run_refunds() -> int:
    with advisory_lock("run_refunds") as acquired:
        if not acquired:
            return 0
        return process_due_refunds()


class Command(BaseCommand):
    help = "Navbatdagi refund'larni bajaradi (har daqiqa)"

    def handle(self, *args, **options):
        self.stdout.write(f"Refund ishlandi: {run_refunds()}")
