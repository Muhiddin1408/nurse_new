"""
payment/management/commands/reconcile_payments.py — kunlik ish (har kecha 03:00)

    python manage.py reconcile_payments                         # kecha, ichki tekshiruvlar
    python manage.py reconcile_payments --date 2026-09-14
    python manage.py reconcile_payments --provider payme --file reestr.csv

Reestr fayli berilmasa, provayder statement API'si sinab ko'riladi; u ulanmagan
bo'lsa tashqi qism o'tkazib yuboriladi (ogohlantirish bilan), ichki tekshiruvlar
baribir ishlaydi.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand

from api.locks import advisory_lock
from api.payments import reconciliation
from api.payments.providers import ManualRefundRequired, ProviderError, get_provider


class Command(BaseCommand):
    help = "To'lovlarni provayder bilan va ichki daftar bilan solishtiradi (B7)"

    def add_arguments(self, parser):
        parser.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD (default: kecha)")
        parser.add_argument("--provider", default="payme")
        parser.add_argument("--file", help="Provayder reestri (CSV)")

    def handle(self, *args, **options):
        day = options["date"] or (datetime.now(reconciliation.LOCAL_TZ).date() - timedelta(days=1))
        provider = options["provider"]

        with advisory_lock("reconcile_payments") as acquired:
            if not acquired:
                self.stdout.write("Boshqa nusxa ishlayapti — o'tkazib yuborildi")
                return

            external = []
            txs = None
            if options["file"]:
                txs = reconciliation.parse_payme_registry(options["file"])
            else:
                start = datetime.combine(day, time.min, tzinfo=reconciliation.LOCAL_TZ)
                try:
                    txs = get_provider(provider).get_statement(start, start + timedelta(days=1))
                except (ManualRefundRequired, ProviderError) as exc:
                    self.stderr.write(f"Tashqi solishtirish o'tkazib yuborildi: {exc}")
            if txs is not None:
                external = reconciliation.reconcile_provider(provider, day, txs)

            internal = reconciliation.reconcile_internal(day)

        total = len(external) + len(internal)
        msg = f"{day}: tashqi farqlar {len(external)}, ichki farqlar {len(internal)}"
        if total:
            self.stderr.write(self.style.ERROR(msg))
        else:
            self.stdout.write(self.style.SUCCESS(msg))
