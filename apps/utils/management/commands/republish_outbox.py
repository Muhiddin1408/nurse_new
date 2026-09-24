"""
utils/management/commands/republish_outbox.py — FAILED hodisalarni qayta yuborish (E3)

    python manage.py republish_outbox --event-id <uuid>
    python manage.py republish_outbox --since 2026-09-14T00:00 --type BookingConfirmed --dry-run

Tanlangan hodisalar `pending` holatiga qaytariladi (attempts=0) va publisher
ularni keyingi siklda yuboradi. Consumer'lar idempotent — takror yetkazish xavfsiz.
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.utils.models import OutboxEvent


class Command(BaseCommand):
    help = "FAILED (yoki tanlangan) outbox hodisalarini qayta yuborish navbatiga qo'yadi"

    def add_arguments(self, parser):
        parser.add_argument("--event-id", help="Bitta hodisa (holatidan qat'i nazar)")
        parser.add_argument("--since", type=datetime.fromisoformat, help="Shu vaqtdan beri FAILED hodisalar")
        parser.add_argument("--type", dest="event_type", help="Hodisa turi bo'yicha filtr")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if options["event_id"]:
            qs = OutboxEvent.objects.filter(id=options["event_id"])
        elif options["since"]:
            since = options["since"]
            if timezone.is_naive(since):
                since = timezone.make_aware(since)
            qs = OutboxEvent.objects.filter(status=OutboxEvent.Status.FAILED, created_at__gte=since)
        else:
            raise CommandError("--event-id yoki --since kerak")

        if options["event_type"]:
            qs = qs.filter(event_type=options["event_type"])

        count = qs.count()
        if options["dry_run"]:
            for ev in qs[:50]:
                self.stdout.write(f"{ev.id} {ev.event_type} {ev.status} urinish={ev.attempts} xato={ev.last_error[:80]}")
            self.stdout.write(f"[dry-run] qayta yuboriladi: {count}")
            return

        qs.update(
            status=OutboxEvent.Status.PENDING, attempts=0,
            next_retry_at=timezone.now(), published_at=None,
        )
        self.stdout.write(self.style.SUCCESS(f"Qayta navbatga qo'yildi: {count}"))
