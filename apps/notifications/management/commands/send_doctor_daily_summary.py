"""Har kuni 08:00 (Toshkent): shifokorlarga bugungi qabullar xulosasi (D9)."""

from django.core.management.base import BaseCommand

from api.doctor.digest import send_daily_summaries
from api.locks import advisory_lock


class Command(BaseCommand):
    help = "Shifokorlarga ertalabki xulosa yuboradi"

    def handle(self, *args, **options):
        with advisory_lock("send_doctor_daily_summary") as acquired:
            if not acquired:
                return
            sent = send_daily_summaries()
        self.stdout.write(f"Yuborildi: {sent}")
