
import uuid

from django.db import models
from django.utils import timezone


class BaseModel(models.Model):
    """Barcha modellar uchun umumiy asos.

    UUID ishlatamiz, chunki keyinchalik servislarga ajratganda avtoinkrement
    id'lar to'qnashadi — har bir servisning o'z bazasi bo'ladi.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OutboxEvent(models.Model):
    """Yuborilishi kerak bo'lgan hodisa.

    Bu jadval domen jadvali BILAN BIRGA, bitta tranzaksiyada yoziladi.
    Shuning uchun uni yozish muvaffaqiyati domen yozuvining muvaffaqiyatiga
    BOG'LANGAN — biri bo'lmasa, ikkinchisi ham bo'lmaydi.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        PUBLISHED = "published", "Yuborilgan"
        FAILED = "failed", "Xato (qo'lda tekshirish kerak)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Kafka topic va partition kaliti
    topic = models.CharField(max_length=100, db_index=True)
    key = models.CharField(max_length=200)  # masalan booking_id — tartib shu bo'yicha kafolatlanadi

    event_type = models.CharField(max_length=100)  # "BookingConfirmed"
    payload = models.JSONField()

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    # E3: exponential backoff — xato bo'lsa shu vaqtgacha qayta urinilmaydi
    next_retry_at = models.DateTimeField(default=timezone.now)
    # E13: hodisani yaratgan HTTP so'rov / jarayon identifikatori
    correlation_id = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            # Publisher shu indeksdan foydalanib navbatdagi ishni topadi
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["status", "next_retry_at"]),
        ]


class DeadLetterEvent(models.Model):
    """E5: consumer ishlov bera olmagan DOIMIY buzuq xabar.

    Consumer uni shu yerga yozib, offset'ni siljitadi — partition bloklanmaydi.
    Tuzatilgandan keyin `republish_outbox` bilan qayta yuborish mumkin.
    """

    consumer = models.CharField(max_length=64)
    topic = models.CharField(max_length=100)
    partition = models.IntegerField(null=True, blank=True)
    offset = models.BigIntegerField(null=True, blank=True)
    event_id = models.CharField(max_length=64, blank=True)
    event_type = models.CharField(max_length=100, blank=True)
    raw = models.TextField()
    error = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("AuditLog o'zgartirilmaydi")

    def delete(self):
        # Yagona istisno — saqlash muddati o'tganlarni tozalash (A10)
        if not getattr(self, "_retention_purge", False):
            raise PermissionError("AuditLog o'chirilmaydi")
        return super().delete()


class AuditLog(models.Model):
    """A11: kim, qachon, nimani o'zgartirdi. FAQAT INSERT.

    Model darajasida UPDATE/DELETE taqiqlangan; PostgreSQL'da qo'shimcha
    ravishda trigger (migratsiya 0004) — ORM'ni chetlab o'tgan SQL ham to'xtaydi.
    """

    actor = models.ForeignKey(
        "account.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    actor_role = models.CharField(max_length=30, blank=True)  # yoki "system"
    action = models.CharField(max_length=64, db_index=True)   # "booking.complete", "doctor.approve"
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64, db_index=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    correlation_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["object_type", "object_id", "-created_at"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("AuditLog o'zgartirilmaydi")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog o'chirilmaydi")
