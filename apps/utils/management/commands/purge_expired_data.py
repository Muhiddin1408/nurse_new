"""A10 + E14: saqlash muddati o'tgan ma'lumotlarni tozalash. Kuniga bir marta (cron).

Muddatlar `settings.DATA_RETENTION` da. Buxgalteriya yozuvlari (Payment,
Refund, Ledger, Payout) va bronlar bu yerda YO'Q — ular qonuniy muddatgacha
saqlanadi; foydalanuvchi o'chirilganda ular anonimlashtiriladi (A10).

E14 — ma'lumot o'sishi:
    Har bir jadval uchun "bu qancha o'sadi va eskilarini kim o'chiradi?"
    savoliga javob `docs/MALUMOT_OSISHI.md` da yozilgan; javobi "shu job"
    bo'lgan jadvallar aynan shu faylda.

    O'chirish BO'LAKLAB (`CHUNK`) bajariladi. Bitta `DELETE` bilan million
    qator o'chirish tranzaksiyani uzaytiradi, jadvalni qulflaydi va
    replikatsiya lagini oshiradi — ya'ni tozalash ishi prodni to'xtatib
    qo'yishi mumkin. Bo'lakma-bo'lak o'chirishda har bir tranzaksiya qisqa
    va job istalgan payt uzilsa ham qolganini keyingi safar davom ettiradi
    (o'chirish idempotent).
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

# Bitta tranzaksiyada o'chiriladigan maksimal qator. Kichikroq = qulf qisqaroq,
# lekin ko'proq aylanish. 5000 — ikkalasining o'rtasi.
CHUNK = 5000
# Bitta yurishdagi maksimal bo'lak. Cheksiz sikl bo'lmasin: backlog juda katta
# bo'lsa job tugaydi va ertaga davom etadi.
MAX_CHUNKS = 200


def _purge_qs(qs) -> int:
    """Querysetni bo'laklab o'chiradi. Qaytaradi: o'chirilgan qatorlar soni."""
    total = 0
    for _ in range(MAX_CHUNKS):
        ids = list(qs.values_list("pk", flat=True)[:CHUNK])
        if not ids:
            break
        with transaction.atomic():
            total += qs.model.objects.filter(pk__in=ids).delete()[0]
        if len(ids) < CHUNK:
            break
    return total


def purge(now=None) -> dict[str, int]:
    from apps.account.models import OtpCode
    from apps.booking.models import Booking, WaitlistEntry
    from apps.notifications.models import ProcessedEvent, SmsLog
    from apps.payment.models import PaymeCallbackLog
    from apps.schedule.models import TimeSlot
    from apps.utils.models import AuditLog, OutboxEvent

    now = now or timezone.now()
    r = settings.DATA_RETENTION

    def before(days):
        return now - timedelta(days=days)

    result = {
        "otp_codes": _purge_qs(OtpCode.objects.filter(created_at__lt=before(r["otp_codes_days"]))),
        "sms_log": _purge_qs(SmsLog.objects.filter(created_at__lt=before(r["sms_log_days"]))),
        "outbox": _purge_qs(
            OutboxEvent.objects.filter(
                status=OutboxEvent.Status.PUBLISHED, created_at__lt=before(r["outbox_published_days"])
            )
        ),
        "processed_events": _purge_qs(
            ProcessedEvent.objects.filter(processed_at__lt=before(r["processed_events_days"]))
        ),
        "payme_callback_log": _purge_qs(
            PaymeCallbackLog.objects.filter(created_at__lt=before(r["payme_callback_log_days"]))
        ),
        # E14: eng tez o'sadigan jadval. 1000 shifokor × kuniga ~16 slot =
        # oyiga ~500K qator. O'tgan va HECH QACHON bron qilinmagan slotlar
        # hech kimga kerak emas: ular shunchaki generatsiya qoldig'i.
        #
        # Bronga bog'langan slot O'CHIRILMAYDI — `Booking.slot` PROTECT, va
        # bu to'g'ri: bron tarixi slot vaqtiga tayanadi. Shuning uchun
        # `Exists` bilan oldindan filtrlaymiz, aks holda job `ProtectedError`
        # bilan yiqilardi.
        "time_slots": _purge_qs(
            TimeSlot.objects.filter(start_at__lt=before(r["time_slots_days"]))
            .annotate(has_booking=Exists(Booking.objects.filter(slot_id=OuterRef("pk"))))
            .filter(has_booking=False)
        ),
        # C10: YOPILGAN navbat yozuvlari — talab signali analitikaga
        # allaqachon hodisa sifatida ketgan, jadvalda saqlashning ma'nosi yo'q.
        # `active` va `notified` TEGILMAYDI: ular hali javob kutmoqda.
        "waitlist": _purge_qs(
            WaitlistEntry.objects.filter(created_at__lt=before(r["waitlist_days"])).exclude(
                status__in=[WaitlistEntry.Status.ACTIVE, WaitlistEntry.Status.NOTIFIED]
            )
        ),
    }

    with transaction.atomic():
        if connection.vendor == "postgresql":
            # Trigger (utils 0004) faqat shu sozlama bilan DELETE'ga ruxsat beradi
            with connection.cursor() as cur:
                cur.execute("SET LOCAL medbron.audit_purge = 'on'")
        qs = AuditLog.objects.filter(created_at__lt=before(r["audit_log_days"]))
        qs._retention_purge = True
        result["audit_log"] = qs.delete()[0]
    return result


class Command(BaseCommand):
    help = "Saqlash muddati o'tgan texnik ma'lumotlarni o'chiradi (A10, E14)"

    def handle(self, *args, **options):
        from api.observability import metrics

        for name, count in purge().items():
            metrics.rows_purged.labels(table=name).inc(count)
            self.stdout.write(f"{name}: {count}")
