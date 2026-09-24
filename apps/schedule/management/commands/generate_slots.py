"""
schedule/management/commands/generate_slots.py — davriy ish (har kecha)

WorkingRule'lardan oldinga `GENERATION_HORIZON_DAYS` kunlik TimeSlot yaratadi.

Ishlatish:
    python manage.py generate_slots                    # barcha shifokorlar
    python manage.py generate_slots --days 14
    python manage.py generate_slots --doctor <uuid>    # bitta shifokor

IDEMPOTENT: ikki marta ishga tushib ketsa ham dublikat yaratmaydi
(`uniq_doctor_slot_start` + `ignore_conflicts`). Shuning uchun CronJob'da
`concurrencyPolicy: Forbid` qulaylik uchun, kafolat uchun emas.
"""

from __future__ import annotations

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from api.locks import advisory_lock
from api.schedule.services import (
    GENERATION_HORIZON_DAYS,
    generate_slots,
    generate_slots_for_all_doctors,
)


class Command(BaseCommand):
    help = "WorkingRule'lardan oldinga slotlar yaratadi (har kecha ishlaydi)"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=GENERATION_HORIZON_DAYS)
        parser.add_argument("--doctor", type=UUID, help="Faqat shu shifokor uchun")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days kamida 1 bo'lishi kerak")

        if options["doctor"]:
            count = generate_slots(options["doctor"], days=days)
            self.stdout.write(self.style.SUCCESS(f"Tayyor: {count} ta slot"))
            return

        with advisory_lock("generate_slots") as acquired:
            if not acquired:
                self.stdout.write("Boshqa nusxa ishlayapti — o'tkazib yuborildi")
                return
            doctors, count = generate_slots_for_all_doctors(days=days)
        self.stdout.write(
            self.style.SUCCESS(f"Tayyor: {doctors} ta shifokor, {count} ta slot")
        )
