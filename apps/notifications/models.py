import uuid

from django.db import models

# Create your models here.

class ProcessedEvent(models.Model):
    """Ishlov berilgan hodisalar reestri — idempotentlik shu orqali."""

    event_id = models.UUIDField(primary_key=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["processed_at"])]  # eskilarini tozalash uchun


class SmsLog(models.Model):
    """Yuborilgan har bir SMS'ning yozuvi — audit va nizolarni hal qilish uchun.

    "Mijoz SMS kelmadi deyapti" degan murojaatga javob berish uchun bu
    jadval shart. Kontent saqlanmaydi (shablon nomi yetarli) — shaxsiy
    ma'lumotni keraksiz joyda ko'paytirmaslik uchun.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Yuborildi"
        FAILED = "failed", "Xato"

    phone = models.CharField(max_length=20, db_index=True)
    template = models.CharField(max_length=100)
    status = models.CharField(max_length=10, choices=Status.choices)
    provider_message_id = models.CharField(max_length=255, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class ScheduledNotification(models.Model):
    """C9: kelajakdagi ma'lum vaqtga rejalashtirilgan xabar.

    NEGA ALOHIDA JADVAL, Kafka'dagi kechiktirilgan xabar emas:
    eslatma bron bekor qilinganda BEKOR QILINISHI kerak, va "qancha eslatma
    navbatda turibdi" savoliga javob bo'lishi kerak. Kafka'da yuborilgan
    xabarni qaytarib olib bo'lmaydi, jadvaldagi qatorni esa `cancelled`
    qilish bitta `UPDATE`.

    Idempotentlik: UNIQUE(booking, kind) — bir bronga bir turdagi eslatma
    faqat bitta. `BookingConfirmed` hodisasi takrorlansa (Kafka "kamida bir
    marta" kafolati) ikkinchi qator yaratilmaydi.
    """

    class Kind(models.TextChoices):
        REMINDER_24H = "reminder_24h", "Eslatma — 24 soat oldin"
        REMINDER_2H = "reminder_2h", "Eslatma — 2 soat oldin"
        REVIEW_REQUEST = "review_request", "Baholash so'rovi"

    class Status(models.TextChoices):
        PENDING = "pending", "Navbatda"
        SENT = "sent", "Yuborildi"
        CANCELLED = "cancelled", "Bekor qilindi"
        # Yuborish vaqtida bron holati mos kelmadi (masalan bekor qilingan,
        # lekin qator `cancelled` bo'lib ulgurmagan). Xato emas — normal hol.
        SKIPPED = "skipped", "O'tkazib yuborildi"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    booking = models.ForeignKey(
        "booking.Booking", on_delete=models.CASCADE, related_name="scheduled_notifications"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)

    # Kimga va nima — REJALASHTIRISH PAYTIDAGI snapshot. Yuborish vaqtida
    # bazaga qaytib murojaat qilmaslik uchun (hodisa payload'i bilan bir xil
    # sabab): eslatma "bron o'shanda shunday edi" degan suratni yuboradi.
    phone = models.CharField(max_length=20)
    params = models.JSONField(default=dict)
    language = models.CharField(max_length=2, default="uz")

    send_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["booking", "kind"], name="uniq_scheduled_notification"),
        ]
        indexes = [
            # Scheduler navbatdagi ishni aynan shu indeks bilan topadi
            models.Index(fields=["status", "send_at"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.send_at:%d.%m %H:%M}"


class NotificationPreference(models.Model):
    """C13: mijoz qaysi kanal orqali xabar oladi.

    Kanal ustuvorligi: push (bepul) > Telegram (bepul) > SMS (pullik, zaxira).
    Tranzaksion xabarlar (bron, to'lov, eslatma) — hech bo'lmasa bitta kanal
    orqali YETKAZILADI: boshqa kanal ishlamasa SMS o'chirilgan bo'lsa ham yuboriladi.
    Marketing — faqat rozilik bilan (`marketing_opt_in`, qonuniy talab) va SMS'siz.
    """

    user = models.OneToOneField("account.User", on_delete=models.CASCADE, related_name="notification_pref")
    push_enabled = models.BooleanField(default=True)
    telegram_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=True)
    marketing_opt_in = models.BooleanField(default=False)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True, unique=True)
    updated_at = models.DateTimeField(auto_now=True)


class PushDevice(models.Model):
    """C13: mobil ilova push tokeni (FCM). Bitta foydalanuvchi — bir nechta qurilma."""

    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    user = models.ForeignKey("account.User", on_delete=models.CASCADE, related_name="push_devices")
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=10, choices=Platform.choices)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "is_active"])]


class NotificationTemplate(models.Model):
    """C13: xabar matni BAZADA — matnni o'zgartirish uchun deploy kerak emas.

    Bo'lmasa kod ichidagi default (`api/notifications/templates.py`) ishlatiladi.
    Parametrlar (`{number}`, `{when}`) kod default'i bilan bir xil bo'lishi shart —
    saqlashda tekshiriladi."""

    key = models.CharField(max_length=64)
    language = models.CharField(max_length=2, choices=[("uz", "uz"), ("ru", "ru")])
    text = models.TextField()
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "language"], name="uniq_notification_template")]

    def __str__(self):
        return f"{self.key} [{self.language}]"
