"""
loadtest/management/commands/export_doctor_ids.py — Faza 6

k6 yuk testi uchun real shifokor ID'larini JSON faylga chiqaradi.

Ishlatish:
    python manage.py export_doctor_ids > loadtest/doctor_ids.json
    python manage.py export_doctor_ids --limit 500 --out loadtest/doctor_ids.json

NEGA REAL ID KERAK:
    k6 mavjud bo'lmagan ID'ga so'rov yuborsa, endpoint 404 qaytaradi va
    bu bazaga umuman bormaydi — ya'ni siz keshni yoki bazani emas, faqat
    "topilmadi" yo'lini test qilib qo'yasiz. Yuk testining ma'nosi
    yo'qoladi. Shuning uchun ID'lar haqiqatan bazada bo'lishi shart.

NEGA KO'P ID KERAK (bitta emas):
    Bitta ID bilan test qilsangiz, birinchi so'rovdan keyin hammasi
    keshdan keladi va siz soxta "ajoyib" natija ko'rasiz. Real trafik
    minglab shifokorga taqsimlanadi va kesh hit darajasi ancha past
    bo'ladi. Shuning uchun mumkin qadar ko'p turli ID eksport qiling.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "k6 yuk testi uchun shifokor ID'larini JSON ro'yxat sifatida chiqaradi"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1000, help="Nechta ID chiqarilsin")
        parser.add_argument(
            "--out",
            type=str,
            default=None,
            help="Fayl yo'li (berilmasa stdout'ga chiqadi)",
        )
        parser.add_argument(
            "--all-statuses",
            action="store_true",
            help="Faqat APPROVED emas, barcha shifokorlar (odatda kerak emas)",
        )

    def handle(self, *args, **options):
        from apps.catalog.models import Doctor

        qs = Doctor.objects.all()
        if not options["all_statuses"]:
            # Yuk testi mijoz ko'radigan holatni sinashi kerak — ya'ni
            # faqat bron qabul qiladigan (APPROVED) shifokorlar
            qs = qs.filter(status=Doctor.Status.APPROVED)

        ids = [str(pk) for pk in qs.values_list("id", flat=True)[: options["limit"]]]

        if not ids:
            self.stderr.write(
                self.style.WARNING(
                    "Hech qanday shifokor topilmadi. Avval test ma'lumot yarating "
                    "(factory_boy seed skripti)."
                )
            )

        payload = json.dumps(ids, indent=2)

        if options["out"]:
            with open(options["out"], "w") as f:
                f.write(payload)
            self.stderr.write(self.style.SUCCESS(f"{len(ids)} ta ID yozildi: {options['out']}"))
        else:
            # stdout'ga toza JSON — shell'da `> fayl.json` bilan yo'naltirish uchun.
            # DIQQAT: bu holatda faqat JSON chiqishi kerak, boshqa hech narsa —
            # shuning uchun status xabarlarini stderr'ga yozamiz, stdout'ga emas.
            self.stdout.write(payload)