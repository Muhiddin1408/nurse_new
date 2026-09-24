"""
catalog/management/commands/check_doctor_licenses.py — kunlik ish (D2)

    python manage.py check_doctor_licenses

Muddati 30 kun ichida tugaydigan litsenziyalar -> shifokorga eslatma (bir marta).
Muddati tugagan -> shifokor avtomatik `suspended` (katalogdan va qidiruvdan chiqadi).
"""

from django.core.management.base import BaseCommand

from api.doctor.onboarding import check_licenses
from api.locks import advisory_lock


class Command(BaseCommand):
    help = "Shifokor litsenziyalari muddatini tekshiradi (har kuni)"

    def handle(self, *args, **options):
        with advisory_lock("check_doctor_licenses") as acquired:
            if not acquired:
                return
            reminded, suspended = check_licenses()
        self.stdout.write(f"Eslatma: {reminded}, to'xtatildi: {suspended}")
