"""
search/management/commands/reindex_doctors.py — Faza 5

Elasticsearch indeksini PostgreSQL'dan noldan qayta quradi.

Ishlatish:
    python manage.py reindex_doctors            # oddiy qayta qurish
    python manage.py reindex_doctors --fresh    # avval indeksni butunlay o'chirib

QACHON KERAK:
    - ES o'lgan yoki ma'lumot buzilgan
    - mapping o'zgargan (DOCTOR_MAPPING yangilangan)
    - loyihaga qidiruv keyin qo'shilgan — mavjud shifokorlarni bir marta yuklash
    - Kafka projector ma'lum muddat ishlamay qolgan va ES orqada qolib ketgan

Bu buyruq CQRS'ning asosiy va'dasini amalda isbotlaydi: o'qish modeli
(ES) to'liq qayta tiklanuvchi, chunki haqiqat manbai (PostgreSQL) alohida.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from api.search.index import DOCTORS_INDEX, create_index
from api.search.reindex import reindex_all
from api.search.utils import _es_client


class Command(BaseCommand):
    help = "Elasticsearch shifokor indeksini PostgreSQL'dan qayta quradi"

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Qayta qurishdan oldin indeksni butunlay o'chirib tashlash",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Tasdiqlashni so'ramaslik (CI/skript uchun)",
        )

    def handle(self, *args, **options):
        # `_es_client()` aynan shu yerda chaqiriladi, modul darajasida emas —
        # aks holda `manage.py help` ham ES ulanishini majburlagan bo'lardi.
        es = _es_client()

        # ES tirikligini oldindan tekshiramiz — yarim yo'lda yiqilmaslik uchun
        try:
            if not es.ping():
                raise CommandError("Elasticsearch javob bermayapti — ulanishni tekshiring")
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Elasticsearch'ga ulanib bo'lmadi: {exc}") from exc

        if options["fresh"]:
            self._confirm_or_exit(options["yes"], DOCTORS_INDEX)
            if es.indices.exists(index=DOCTORS_INDEX):
                es.indices.delete(index=DOCTORS_INDEX)
                self.stdout.write(self.style.WARNING(f"Indeks o'chirildi: {DOCTORS_INDEX}"))
            create_index(es)

        self.stdout.write("Qayta indekslash boshlandi...")
        started = time.monotonic()

        try:
            count = reindex_all()
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Qayta indekslashda xato: {exc}") from exc

        elapsed = time.monotonic() - started
        self.stdout.write(
            self.style.SUCCESS(
                f"Tayyor: {count} ta shifokor {elapsed:.1f} soniyada indekslandi"
            )
        )

    def _confirm_or_exit(self, auto_yes: bool, index_name: str) -> None:
        """--fresh xavfli: mavjud indeksni o'chiradi. Tasodifan ishga
        tushirilmasligi uchun tasdiq so'raymiz."""
        if auto_yes:
            return
        answer = input(
            f"'{index_name}' indeksi butunlay o'chiriladi va qayta quriladi. "
            f"Davom etilsinmi? [yes/N]: "
        )
        if answer.strip().lower() != "yes":
            raise CommandError("Bekor qilindi")