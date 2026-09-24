"""
notifications/scheduler.py — C9: rejalashtirilgan eslatmalar.

    T-24 soat  "Ertaga 14:00 da Dr. Aliyev qabuli"
    T-2  soat  "2 soatdan keyin qabul"
    T+2  soat  "Qabul qanday o'tdi? Baholang"

NEGA BU MUHIM: eslatmasiz no-show darajasi 20–30%, eslatma bilan 10–15%.
Bu to'g'ridan-to'g'ri daromad — har bir kelmagan mijoz to'langan slotni
va shifokorning vaqtini yo'qotadi.

IKKI QATLAMLI HIMOYA (bu faylning asosiy g'oyasi):
    1. Bron bekor qilinganda/muddati o'tganda kutilayotgan qatorlar
       `cancelled` bo'ladi — normal yo'l.
    2. Yuborish vaqtida bron holati BAZADAN QAYTA TEKSHIRILADI.
       Birinchi qatlam ishlamay qolsa ham (hodisa kechikdi, consumer
       to'xtadi, holat admin paneldan qo'lda o'zgartirildi), bekor qilingan
       bron uchun "ertaga qabulingiz bor" SMS'i KETMAYDI.

    Ikkinchi qatlamsiz qilish mumkin edi, lekin noto'g'ri eslatma — mijoz
    ishonchini yo'qotadigan va qo'llab-quvvatlashga murojaat keltiradigan
    xato turi. Bitta qo'shimcha SELECT undan arzonroq.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.notifications.models import ScheduledNotification
from api.notifications import services as notification_services
from api.observability import metrics
from api.schedule.services import LOCAL_TZ

logger = logging.getLogger(__name__)

Kind = ScheduledNotification.Kind
Status = ScheduledNotification.Status

# Har bir tur uchun: (shablon, yuborish uchun kerakli holatlar, kutish
# holatlari). Yuborish PAYTIDAGI bron holati:
#   * kerakli ro'yxatda  -> yuboriladi
#   * kutish ro'yxatida  -> hali erta, KEYINGA SURILADI (pastga qarang)
#   * boshqa (yakuniy)   -> `skipped`, xato emas
KIND_SPEC: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    Kind.REMINDER_24H: ("booking_reminder_24h", frozenset({"confirmed"}), frozenset()),
    Kind.REMINDER_2H: ("booking_reminder_2h", frozenset({"confirmed"}), frozenset()),
    # Baholash so'rovi faqat HAQIQATAN BO'LGAN qabul uchun. `no_show` ga
    # yuborilsa, kelmagan mijozdan sharh so'ragan bo'lardik.
    #
    # ⬇ `confirmed` — KUTISH holati, tashlab yuborish emas. Sharh so'rovi
    # qabuldan 2 soat keyin yuboriladi, lekin shifokor "yakunlandi" ni
    # kechroq bosishi mumkin (yoki umuman bosmasligi — u holda
    # `auto_complete_bookings` 24 soatdan keyin belgilaydi). Kutmasak,
    # sharh so'rovi bronlarning ko'pchiligida hech qachon ketmasdi va C11
    # (reyting) ma'lumotsiz qolardi.
    Kind.REVIEW_REQUEST: (
        "booking_review_request",
        frozenset({"completed"}),
        frozenset({"confirmed"}),
    ),
}

BATCH_SIZE = 200
# Kutish holati uchun: qancha vaqtdan keyin qayta ko'riladi va necha marta.
# 1 soat × 30 = 30 soat — `auto_complete_bookings` (qabuldan 24 soat keyin)
# ishlashiga yetarli zaxira bilan.
DEFER_INTERVAL = timedelta(hours=1)
MAX_DEFERS = 30


# ---------------------------------------------------------------------------
# Rejalashtirish
# ---------------------------------------------------------------------------


def schedule_booking_reminders(
    *,
    booking_id: UUID | str,
    start_at: datetime,
    phone: str,
    number: str,
    place: str = "clinic",
    language: str = "uz",
) -> int:
    """Bron tasdiqlanganda chaqiriladi. Qaytaradi: yaratilgan qatorlar soni.

    IDEMPOTENT: `ignore_conflicts=True` + UNIQUE(booking, kind). Kafka
    `BookingConfirmed` ni ikki marta yetkazsa, ikkinchi chaqiruv hech narsa
    qilmaydi va mijoz ikkita bir xil SMS olmaydi.
    """
    conf = settings.BOOKING_REMINDERS
    now = timezone.now()
    when = _fmt_when(start_at)
    place_label = "Uy chaqiruvi" if place == "home" else "Klinikada qabul"

    plan = [
        (Kind.REMINDER_24H, start_at - timedelta(hours=conf["first_hours"]),
         {"when": when, "place": place_label}),
        (Kind.REMINDER_2H, start_at - timedelta(hours=conf["second_hours"]),
         {"when": when, "place": place_label}),
        (Kind.REVIEW_REQUEST, start_at + timedelta(hours=conf["review_after_hours"]),
         {"number": number}),
    ]

    rows = []
    for kind, send_at, params in plan:
        # ⬇ Vaqti ALLAQACHON o'tgan eslatmani yaratmaymiz. Mijoz qabuldan
        # 3 soat oldin bron qilsa, T-24 eslatmasi ma'nosiz — uni yaratsak,
        # scheduler uni darhol yuborar va mijoz "ertaga qabul" SMS'ini
        # qabulga 3 soat qolganda olardi.
        if send_at <= now and kind != Kind.REVIEW_REQUEST:
            continue
        rows.append(
            ScheduledNotification(
                booking_id=booking_id,
                kind=kind,
                phone=phone,
                params=params,
                language=language,
                send_at=send_at,
            )
        )

    if not rows:
        return 0
    created = ScheduledNotification.objects.bulk_create(rows, ignore_conflicts=True)
    logger.info("Eslatmalar rejalashtirildi: bron=%s, soni=%s", number, len(created))
    return len(created)


def reschedule_booking_reminders(**kwargs) -> int:
    """C3: bron boshqa vaqtga ko'chirildi — eski eslatmalar endi noto'g'ri.

    Yopib qo'yish yetmaydi: UNIQUE(booking, kind) tufayli yangi qator
    yaratilmay qolardi va ko'chirilgan bron eslatmasiz qolardi. Shuning
    uchun YUBORILMAGAN qatorlar o'chiriladi va qaytadan rejalashtiriladi.

    Yuborilgan (`sent`) qatorlar qoladi — jurnal o'zgartirilmaydi, va
    `ignore_conflicts` tufayli ular o'rniga yangisi yaratilmaydi.
    """
    ScheduledNotification.objects.filter(booking_id=kwargs["booking_id"]).exclude(
        status=Status.SENT
    ).delete()
    return schedule_booking_reminders(**kwargs)


def cancel_booking_reminders(booking_id: UUID | str) -> int:
    """Bron bekor bo'ldi / muddati o'tdi — kutilayotgan xabarlarni to'xtatadi.

    Faqat `pending` qatorlar. Yuborilgan SMS'ni qaytarib bo'lmaydi va
    `sent` qatorni o'zgartirish jurnalni buzadi.
    """
    return ScheduledNotification.objects.filter(
        booking_id=booking_id, status=Status.PENDING
    ).update(status=Status.CANCELLED)


# ---------------------------------------------------------------------------
# Yuborish (cron: har daqiqa)
# ---------------------------------------------------------------------------


def run_due_notifications(limit: int = BATCH_SIZE) -> int:
    """Vaqti kelgan xabarlarni yuboradi. Qaytaradi: haqiqatan yuborilganlar soni.

    SKIP LOCKED — bir necha nusxa parallel ishlashi mumkin, bitta qator
    ikki marta olinmaydi (`run_outbox_publisher` bilan bir xil naqsh).
    """
    now = timezone.now()
    sent = 0

    with transaction.atomic():
        due = list(
            ScheduledNotification.objects.select_for_update(skip_locked=True)
            .filter(status=Status.PENDING, send_at__lte=now)
            .order_by("send_at")[:limit]
        )
        for item in due:
            if _send_one(item):
                sent += 1

    metrics.scheduled_notifications_backlog.set(_backlog())
    return sent


def _send_one(item: ScheduledNotification) -> bool:
    template, allowed_statuses, defer_statuses = KIND_SPEC[item.kind]
    status = _booking_status(item.booking_id)

    if status in defer_statuses and item.attempts < MAX_DEFERS:
        # Hali erta — keyinroq qayta ko'riladi. `attempts` shu yerda
        # kechiktirishlar hisoblagichi bo'lib xizmat qiladi.
        item.attempts += 1
        item.send_at = timezone.now() + DEFER_INTERVAL
        item.last_error = f"kutilmoqda: {status}"
        item.save(update_fields=["attempts", "send_at", "last_error"])
        return False

    if status not in allowed_statuses:
        # Ikkinchi himoya qatlami ishladi (fayl boshidagi izoh).
        item.status = Status.SKIPPED
        item.last_error = f"bron holati: {status}"
        item.save(update_fields=["status", "last_error"])
        metrics.scheduled_notifications_sent.labels(kind=item.kind, result="skipped").inc()
        return False

    # send_sms ATAYLAB xato ko'tarmaydi (fail-safe) — natijani SmsLog
    # yozadi. Bu yerda urinishni hisoblaymiz va qatorni yopamiz: aks holda
    # provayder yiqilganda bitta xabar cheksiz qayta yuborilardi.
    notification_services.notify(
        phone=item.phone, template=template, params=item.params, language=item.language
    )
    item.status = Status.SENT
    item.attempts += 1
    item.sent_at = timezone.now()
    item.save(update_fields=["status", "attempts", "sent_at"])
    metrics.scheduled_notifications_sent.labels(kind=item.kind, result="sent").inc()
    return True


def _booking_status(booking_id) -> str:
    from apps.booking.models import Booking

    return (
        Booking.objects.filter(id=booking_id).values_list("status", flat=True).first() or "missing"
    )


def _backlog() -> int:
    return ScheduledNotification.objects.filter(
        status=Status.PENDING, send_at__lte=timezone.now()
    ).count()


def _fmt_when(start_at: datetime) -> str:
    """Mijozga ko'rinadigan vaqt — HAR DOIM Toshkent vaqtida (C8 qoidasi)."""
    return start_at.astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")
