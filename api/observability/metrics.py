# RED metodikasi: har bir servis uchun uchta narsani o'lchang —
#   Rate      (sekundiga necha so'rov)
#   Errors    (nechtasi xato bilan tugadi)
#   Duration  (qancha vaqt oldi)
# Bu uchtasi tizim "salomatligi"ni 90% tasvirlaydi. Boshqa metrikalarni
# keyin, kerak bo'lganda qo'shasiz.

from prometheus_client import Counter, Gauge, Histogram

# Booking domeni uchun BIZNES metrikalari — texnik metrikalar (so'rov soni)
# ustiga. Bular Grafana'da biznes dashboard'ini quradi.
bookings_created = Counter(
    "medbron_bookings_created_total",
    "Yaratilgan bronlar soni",
    ["doctor_specialization", "place"],  # yorliqlar bo'yicha ajratib ko'rish mumkin
)

bookings_failed = Counter(
    "medbron_bookings_failed_total",
    "Muvaffaqiyatsiz bron urinishlari",
    ["reason"],  # "slot_taken", "payment_failed", "validation"
)

booking_duration = Histogram(
    "medbron_booking_duration_seconds",
    "Bron yaratish jarayoni davomiyligi",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],  # p95/p99 shu bucketlardan hisoblanadi
)

# Payment saga uchun — bu ayniqsa muhim, chunki bu yerda PUL bor
payment_webhook_received = Counter(
    "medbron_payment_webhook_total", "Kelgan to'lov webhook'lari", ["method", "result"]
)

saga_compensations = Counter(
    "medbron_saga_compensations_total",
    "Ishga tushgan kompensatsiyalar (to'lov bekor -> bron bekor)",
)
# ⬆ Bu metrika o'sib ketsa — ALERT. Ko'p kompensatsiya = ko'p mijoz to'lay
# olmayapti = to'lov integratsiyasida muammo bor.

# E2: outbox — "jim ishlamay qolish"ning asosiy signali.
# lag va pending — scrape paytida bazadan olinadi (OutboxCollector), publisher
# jarayonidan EMAS: publisher o'lsa, o'zi haqida xabar bera olmaydi.
outbox_published = Counter("medbron_outbox_published_total", "Kafka'ga yetkazilgan hodisalar")
outbox_failed = Counter("medbron_outbox_failed_total", "MAX_ATTEMPTS dan keyin FAILED bo'lgan hodisalar")
outbox_publish_duration = Histogram(
    "medbron_outbox_publish_duration_seconds", "Bitta partiyani yetkazish vaqti",
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 10],
)

# E5: doimiy buzuq xabarlar (DLQ ga tushdi)
consumer_poison = Counter(
    "medbron_consumer_poison_total", "DLQ ga yozilgan buzuq xabarlar", ["consumer"]
)


class OutboxCollector:
    """Scrape paytida bazadan outbox holatini o'qiydi."""

    def collect(self):
        from django.db.models import Count, Min
        from django.utils import timezone
        from prometheus_client.core import GaugeMetricFamily

        from apps.utils.models import OutboxEvent

        lag = GaugeMetricFamily("medbron_outbox_lag_seconds", "Eng eski pending hodisaning yoshi")
        pending = GaugeMetricFamily("medbron_outbox_pending_total", "Pending hodisalar soni")
        failed_now = GaugeMetricFamily("medbron_outbox_failed_current", "Hozir FAILED holatdagi hodisalar")
        try:
            agg = OutboxEvent.objects.filter(status=OutboxEvent.Status.PENDING).aggregate(
                n=Count("id"), oldest=Min("created_at")
            )
            n_failed = OutboxEvent.objects.filter(status=OutboxEvent.Status.FAILED).count()
        except Exception:  # noqa: BLE001 — scrape hech qachon yiqilmasin
            return
        oldest = agg["oldest"]
        lag.add_metric([], (timezone.now() - oldest).total_seconds() if oldest else 0.0)
        pending.add_metric([], agg["n"])
        failed_now.add_metric([], n_failed)
        yield lag
        yield pending
        yield failed_now


# E9: expire_bookings ortda qolyaptimi. Alert: backlog > 5000
expire_backlog = Gauge(
    "medbron_expire_backlog", "Tozalanmagan muddati o'tgan hold'lar soni"
)

# B13: to'lov kuzatuvchanligi. Eng muhim alert: succeeded soatiga 0 ga tushsa.
payment_checkout_created = Counter(
    "medbron_payment_checkout_created_total", "Yaratilgan checkout'lar", ["provider"]
)
payment_succeeded = Counter(
    "medbron_payment_succeeded_total", "Muvaffaqiyatli to'lovlar", ["provider"]
)
payment_duration = Histogram(
    "medbron_payment_duration_seconds",
    "Checkout -> succeeded davomiyligi",
    ["provider"],
    buckets=[10, 30, 60, 120, 300, 600, 900, 1800],
)
payment_refund_required = Counter(
    "medbron_payment_refund_required_total",
    "Pul yechildi, lekin bronni tasdiqlab bo'lmadi — refund kerak",
    ["provider"],
)
reconciliation_discrepancies = Counter(
    "medbron_reconciliation_discrepancies_total", "Reconciliation farqlari", ["type"]
)
refunds_total = Counter(
    "medbron_refunds_total", "Refund natijalari", ["reason", "status"]
)
provider_errors = Counter(
    "medbron_provider_errors_total", "Provayder xato javoblari", ["provider", "code"]
)

# C3: ko'chirish. Bu raqam bekor qilishdan SAQLAB QOLINGAN bronlar soni —
# `bookings_cancelled` bilan birga o'qiladi.
bookings_rescheduled = Counter(
    "medbron_bookings_rescheduled_total", "Boshqa vaqtga ko'chirilgan bronlar"
)

# C9: rejalashtirilgan eslatmalar
scheduled_notifications_sent = Counter(
    "medbron_scheduled_notifications_total",
    "Rejalashtirilgan xabarlar natijasi",
    ["kind", "result"],  # result="sent" | "skipped" | "failed"
)
# Scheduler ortda qolyaptimi: vaqti kelgan, lekin hali yuborilmaganlar soni.
# Alert: > 100 — cron ishlamayapti yoki SMS provayderi yiqilgan.
scheduled_notifications_backlog = Gauge(
    "medbron_scheduled_notifications_backlog", "Vaqti kelgan, yuborilmagan xabarlar"
)


# ---------------------------------------------------------------------------
# QAYERDA O'LCHANADI
# ---------------------------------------------------------------------------
#
#   bookings_created          -> booking/services.py: create_booking (oxirida)
#   bookings_failed           -> booking/services.py: create_booking
#                                  reason="validation" | "slot_taken"
#   booking_duration          -> booking/services.py: @booking_duration.time()
#                                  dekorator avtomatik vaqtni o'lchaydi
#   payment_webhook_received  -> payments/webhook.py: handle_payme_webhook
#                                  result="ok" | "error" | "unsupported"
#   saga_compensations        -> payments/webhook.py: _cancel
#
# ⚠️ YORLIQ KARDINALLIGI: yorliqqa faqat KAM XIL qiymatli maydon qo'ying.
# Har bir yangi yorliq kombinatsiyasi Prometheus'da alohida time series
# yaratadi. `specialization` (~50 xil) va `place` (2 xil) mos keladi;
# `doctor_id` yoki `booking_id` qo'ysangiz — millionlab seriya va o'lgan
# Prometheus. Bu eng ko'p uchraydigan metrika xatosi.
#
# ⚠️ MULTIPROCESS: gunicorn'ni bir necha worker bilan ishlatsangiz, har bir
# worker o'z hisoblagichini saqlaydi va `/metrics` faqat javob bergan
# worker'nikini ko'rsatadi — raqamlar "sakraydi". Yechim:
# `PROMETHEUS_MULTIPROC_DIR` env + `prometheus_client.multiprocess`.



# A14: OTP. `result` = sent | rejected_country | rate_limited | sms_failed.
# Alert: `sent` soatlik o'rtachadan 3 barobar oshsa — SMS pumping ehtimoli.
otp_requests = Counter("medbron_otp_requests_total", "OTP so'rovlari", ["result"])
otp_verify_failed = Counter("medbron_otp_verify_failed_total", "Muvaffaqiyatsiz OTP tekshiruvlari", ["reason"])


# ---------------------------------------------------------------------------
# E14: ma'lumot o'sishi
# ---------------------------------------------------------------------------

# Tozalash jobi nimani o'chirdi. Alert: `time_slots` bir hafta davomida 0
# bo'lsa — cron o'lgan va jadval jim o'sib boryapti.
rows_purged = Counter(
    "medbron_rows_purged_total", "Saqlash muddati bo'yicha o'chirilgan qatorlar", ["table"]
)


class TableSizeCollector:
    """Eng tez o'sadigan jadvallarning TAXMINIY qator soni.

    `COUNT(*)` EMAS: 18 million qatorli jadvalda u to'liq skan qiladi va har
    bir Prometheus scrape'ida (15 soniyada bir marta) bazani yuklaydi. Buning
    o'rniga `pg_class.reltuples` — `ANALYZE` qoldirgan statistik baho. U bir
    oz eskiroq bo'lishi mumkin, lekin o'SISH TENDENSIYASI uchun aynan yetarli,
    va E14 da kerak bo'lgani ham shu: "qaysi jadval qancha tez o'syapti".

    PostgreSQL'dan boshqa bazada (test — SQLite) jim o'tkazib yuboriladi.
    """

    TABLES = (
        "schedule_timeslot",
        "booking_booking",
        "utils_outboxevent",
        "utils_auditlog",
        "notifications_smslog",
        "notifications_processedevent",
        "payment_paymecallbacklog",
    )

    def collect(self):
        from django.db import connection
        from prometheus_client.core import GaugeMetricFamily

        rows = GaugeMetricFamily(
            "medbron_table_rows_estimate", "Jadvaldagi qatorlarning taxminiy soni", labels=["table"]
        )
        if connection.vendor != "postgresql":
            return
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT relname, reltuples::bigint FROM pg_class WHERE relname = ANY(%s)",
                    [list(self.TABLES)],
                )
                found = cur.fetchall()
        except Exception:  # noqa: BLE001 — scrape hech qachon yiqilmasin
            return
        for name, count in found:
            rows.add_metric([name], max(count, 0))
        yield rows


# C15.4: qidiruv qaysi dvigatel bilan ishladi.
# ⚠️ ENG MUHIM ALERT: `engine="postgres"` noldan oshsa — Elasticsearch
# yiqilgan va qidiruv degradatsiyada ishlayapti. Jim fallback'ning butun
# ma'nosi shu metrikada: usiz ES bir oy o'lik turishi va buni hech kim
# bilmasligi mumkin.
search_requests = Counter(
    "medbron_search_requests_total", "Shifokor qidiruvi so'rovlari", ["engine"]
)
