"""
utils/management/commands/run_scheduler.py — Kubernetes'siz muhit uchun

Barcha davriy ishlarni bitta uzluksiz jarayonda bajaradi:
    - har `--expire-interval` soniyada: expire_bookings
    - ishga tushganda va har `--generate-interval` soniyada: generate_slots

    python manage.py run_scheduler

Prodda (Kubernetes) buning o'rniga `k8s/booking-cronjobs.yaml` dagi CronJob'lar
ishlatiladi. Bu jarayonni FAQAT BITTA nusxada ishga tushiring — ikki nusxa
xavfli emas (ikkala ish ham idempotent), lekin bekorga ikki barobar yuk beradi.
"""

from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from api.doctor.onboarding import check_licenses
from api.schedule.services import generate_slots_for_all_doctors
from apps.booking.management.commands.auto_complete_bookings import run_auto_complete
from apps.booking.management.commands.expire_bookings import run_expiry
from apps.notifications.management.commands.run_notification_scheduler import (
    run_once as run_notifications,
)
from apps.payment.management.commands.run_refunds import run_refunds

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Davriy ishlarni (bron muddati, slot generatsiyasi) uzluksiz bajaradi"

    def add_arguments(self, parser):
        parser.add_argument("--expire-interval", type=int, default=60)
        parser.add_argument("--generate-interval", type=int, default=24 * 60 * 60)
        parser.add_argument("--complete-interval", type=int, default=60 * 60)

    def handle(self, *args, **options):
        expire_every = options["expire_interval"]
        generate_every = options["generate_interval"]
        complete_every = options["complete_interval"]
        next_expire = 0.0
        next_generate = 0.0
        next_complete = 0.0

        self.stdout.write("Scheduler ishga tushdi")
        while True:
            now = time.monotonic()

            if now >= next_generate:
                self._run("generate_slots", generate_slots_for_all_doctors)
                self._run("check_doctor_licenses", check_licenses)
                next_generate = now + generate_every

            if now >= next_expire:
                self._run("expire_bookings", run_expiry)
                self._run("run_refunds", run_refunds)
                # C9: vaqti kelgan eslatmalar — expire bilan bir xil ritmda
                self._run("run_notification_scheduler", run_notifications)
                next_expire = now + expire_every

            if now >= next_complete:
                self._run("auto_complete_bookings", run_auto_complete)
                next_complete = now + complete_every

            time.sleep(max(1.0, min(next_expire, next_generate, next_complete) - time.monotonic()))

    def _run(self, name, func):
        # Uzoq yashaydigan jarayonda uzilgan DB ulanishi qolib ketmasin
        close_old_connections()
        try:
            result = func()
            logger.info("%s: %s", name, result)
        except Exception:  # noqa: BLE001
            # Bitta sikldagi xato scheduler'ni o'ldirmasligi kerak —
            # keyingi daqiqada qayta uriniladi.
            logger.exception("%s xato bilan tugadi", name)
