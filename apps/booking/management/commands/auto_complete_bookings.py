"""
booking/management/commands/auto_complete_bookings.py — davriy ish (har soat)

Shifokor belgilamagan, tugaganiga 24 soatdan oshgan `confirmed` qabullarni
`completed` (by=system) qiladi (C1 zaxira manbai).

    python manage.py auto_complete_bookings
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from api.booking.services import auto_complete_bookings
from api.locks import advisory_lock


def run_auto_complete() -> int:
    with advisory_lock("auto_complete_bookings") as acquired:
        if not acquired:
            return 0
        return auto_complete_bookings()


class Command(BaseCommand):
    help = "Tugagan, belgilanmagan qabullarni avtomatik yakunlaydi (har soat)"

    def handle(self, *args, **options):
        self.stdout.write(f"Avtomatik yakunlandi: {run_auto_complete()}")
