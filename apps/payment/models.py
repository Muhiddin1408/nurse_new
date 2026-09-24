from django.db import models

from apps.utils.models import BaseModel


class Payment(BaseModel):
    """Bitta bronga bir nechta to'lov urinishi bo'lishi mumkin (B1).

    Masalan birinchi urinish Payme'da bekor bo'ldi -> ikkinchisi Click orqali.
    Lekin FAQAT BITTASI `succeeded` bo'la oladi — shartli UNIQUE constraint.

    Holat mashinasi (B9):
        created ──CreateTransaction──> processing ──Perform──> succeeded
           │                              │                      │
           │                              └──Cancel(-1)──> cancelled
           └──timeout──> expired                                 └──Cancel(-2)──> refunded
        failed <- provayder xatosi
    """

    class Provider(models.TextChoices):
        PAYME = "payme", "Payme"
        CLICK = "click", "Click"
        CASH = "cash", "Naqd"

    class Status(models.TextChoices):
        CREATED = "created", "Yaratilgan"
        PROCESSING = "processing", "Jarayonda"
        SUCCEEDED = "succeeded", "Muvaffaqiyatli"
        FAILED = "failed", "Muvaffaqiyatsiz"
        CANCELLED = "cancelled", "Bekor qilingan"
        EXPIRED = "expired", "Muddati o'tgan"
        REFUNDED = "refunded", "Qaytarilgan"
        PARTIALLY_REFUNDED = "partially_refunded", "Qisman qaytarilgan"

    booking = models.ForeignKey("booking.Booking", on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)

    external_id = models.CharField(max_length=255, blank=True)  # provayderdagi ID

    # Idempotentlik kaliti — bir so'rov ikki marta kelsa, pul ikki marta yechilmaydi.
    idempotency_key = models.CharField(max_length=100, unique=True)

    # Provayder protokoli vaqtlari (Payme: millisekund). CheckTransaction va
    # GetStatement ularni AYNAN birinchi javobdagidek qaytarishi shart.
    provider_create_time = models.BigIntegerField(null=True, blank=True)
    perform_time = models.BigIntegerField(null=True, blank=True)
    cancel_time = models.BigIntegerField(null=True, blank=True)
    cancel_reason = models.SmallIntegerField(null=True, blank=True)
    # Provayderga xos holat (Payme state: 1, 2, -1, -2)
    provider_state = models.JSONField(default=dict, blank=True)

    # B3 fail-safe: pul yechildi, lekin bron tasdiqlanmadi -> avtomatik refund kerak
    needs_refund = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(status="succeeded"),
                name="uniq_succeeded_payment_per_booking",
            ),
        ]
        indexes = [
            models.Index(fields=["booking", "status"]),
            models.Index(fields=["provider", "external_id"]),
            models.Index(fields=["provider", "provider_create_time"]),  # GetStatement
        ]


class Refund(BaseModel):
    """Pulni qaytarish (B4). `Payment` dan alohida: bitta to'lovga bir nechta
    qisman refund bo'lishi mumkin.

    Oqim:
        cancel_booking -> Refund(pending) + RefundRequested (bitta tranzaksiyada)
        run_refunds -> provider.refund()
            muvaffaqiyat      -> succeeded, Payment refunded / partially_refunded
            vaqtinchalik xato -> retry (exponential backoff + jitter), max 5
            5 dan keyin        -> failed + ALERT (qo'lda ko'rib chiqish navbati)
            provayder avtomat refund qilolmaydi -> manual_required + ALERT
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        SUCCEEDED = "succeeded", "Qaytarildi"
        FAILED = "failed", "Xato (qo'lda ko'rish kerak)"
        MANUAL_REQUIRED = "manual_required", "Qo'lda qaytarish kerak"

    class Initiator(models.TextChoices):
        CLIENT = "client", "Mijoz"
        DOCTOR = "doctor", "Shifokor"
        ADMIN = "admin", "Admin"
        SYSTEM = "system", "Tizim"

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    booking = models.ForeignKey("booking.Booking", on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=64)  # cancellation reason_code yoki "booking_unavailable"
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    initiated_by = models.CharField(max_length=10, choices=Initiator.choices)

    external_refund_id = models.CharField(max_length=255, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # Idempotentlik: bitta bron + sabab uchun bitta refund
            models.UniqueConstraint(fields=["booking", "reason"], name="uniq_refund_booking_reason"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="refund_amount_positive"),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class LedgerTransaction(BaseModel):
    """Ikki yozuvli daftarning bitta amali (B8). Ichidagi yozuvlar: sum(debit) == sum(credit).

    Faqat INSERT. Xato bo'lsa — o'chirilmaydi, teskari amal yoziladi.
    """

    kind = models.CharField(max_length=40)  # payment_succeeded | refund_succeeded
    ref_type = models.CharField(max_length=20)  # payment | refund
    ref_id = models.UUIDField()
    # Payout kabi bir nechta bronni qamrab oluvchi amallarda bo'sh
    booking = models.ForeignKey("booking.Booking", on_delete=models.PROTECT, related_name="ledger_transactions",
                                null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            # Idempotentlik: bitta hodisa daftarga ikki marta tushmaydi
            models.UniqueConstraint(fields=["kind", "ref_id"], name="uniq_ledger_kind_ref"),
        ]


class LedgerEntry(models.Model):
    class Account(models.TextChoices):
        CLIENT_PAYMENTS = "client_payments", "Mijozlardan tushum (provayderda / bankda)"
        PLATFORM_REVENUE = "platform_revenue", "Platforma daromadi"
        DOCTOR_PAYABLE = "doctor_payable", "Shifokorlarga qarz"
        PROVIDER_FEES = "provider_fees", "Provayder komissiyasi"

    transaction = models.ForeignKey(LedgerTransaction, on_delete=models.PROTECT, related_name="entries")
    account = models.CharField(max_length=30, choices=Account.choices)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.PROTECT, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(debit__gte=0) & models.Q(credit__gte=0)
                           & (models.Q(debit=0) | models.Q(credit=0))),
                name="ledger_entry_one_side_non_negative",
            ),
        ]
        indexes = [models.Index(fields=["account", "doctor"])]


class PayoutAccount(BaseModel):
    """D6: shifokorning to'lov rekvizitlari. Karta/hisob raqami to'liq API javobida qaytmaydi."""

    doctor = models.OneToOneField("catalog.Doctor", on_delete=models.CASCADE, related_name="payout_account")
    holder_name = models.CharField(max_length=255)
    bank_name = models.CharField(max_length=255, blank=True)
    account_number = models.CharField(max_length=34)  # karta (16) yoki hisob raqami (20)
    mfo = models.CharField(max_length=10, blank=True)  # bank kodi
    inn = models.CharField(max_length=14, blank=True)

    @property
    def masked_number(self) -> str:
        return "•••• " + self.account_number[-4:]


class PayoutPeriod(BaseModel):
    """D6/B8: shifokorga to'lov davri (shifokor × hafta).

    Qamrab oladi: yakunlangan qabullar va mijoz kelmagan (`no_show_by=client`)
    qabullar — ikkalasida ham shifokor vaqt sarflagan. Minus refundlarning
    shifokor ulushi. Status: pending (hisoblangan) -> paid (bankka o'tkazildi).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "To'lanishi kutilmoqda"
        PAID = "paid", "To'langan"

    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.PROTECT, related_name="payouts")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    bookings_count = models.PositiveIntegerField(default=0)
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    platform_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    provider_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    refunds_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey("account.User", on_delete=models.PROTECT, null=True, blank=True)
    bank_reference = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["doctor", "period_start", "period_end"], name="uniq_payout_period"),
        ]
        indexes = [models.Index(fields=["status", "period_end"])]


class PayoutLine(models.Model):
    """Bitta bronning payout'dagi satri. UNIQUE(booking) — bron ikki marta to'lanmaydi."""

    payout = models.ForeignKey(PayoutPeriod, on_delete=models.CASCADE, related_name="lines")
    booking = models.OneToOneField("booking.Booking", on_delete=models.PROTECT, related_name="payout_line")
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=12, decimal_places=2)
    provider_fee = models.DecimalField(max_digits=12, decimal_places=2)
    refunds_amount = models.DecimalField(max_digits=12, decimal_places=2)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)


class PaymentDiscrepancy(BaseModel):
    """Reconciliation topgan farq (B7). Maqsad: doimiy 0. Har biri qo'lda hal qilinadi."""

    class Type(models.TextChoices):
        PROVIDER_ONLY = "provider_only", "Provayderda bor, bizda yo'q (pul olindi, xizmat yo'q)"
        LOCAL_ONLY = "local_only", "Bizda succeeded, provayderda yo'q"
        AMOUNT_MISMATCH = "amount_mismatch", "Summa farq qiladi"
        STATUS_MISMATCH = "status_mismatch", "Holat farq qiladi"
        MISSING_LEDGER = "missing_ledger", "To'lov daftarga tushmagan"
        BOOKING_WITHOUT_PAYMENT = "booking_without_payment", "Tasdiqlangan bron, to'lov yo'q"
        UNRESOLVED_REFUND = "unresolved_refund", "Refund kerak, lekin yaratilmagan"
        LEDGER_IMBALANCE = "ledger_imbalance", "Daftar balansi 0 emas"

    class Status(models.TextChoices):
        OPEN = "open", "Ochiq"
        RESOLVED = "resolved", "Hal qilingan"

    type = models.CharField(max_length=30, choices=Type.choices)
    provider = models.CharField(max_length=20, blank=True)
    external_id = models.CharField(max_length=255, blank=True)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, null=True, blank=True)
    booking = models.ForeignKey("booking.Booking", on_delete=models.PROTECT, null=True, blank=True)
    local_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    provider_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    period_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    resolution_note = models.TextField(blank=True)
    # type + obyekt identifikatorlari. NULL'li maydonlar UNIQUE'da bir-biriga
    # teng hisoblanmaydi, shuning uchun dublikatdan himoya shu satr orqali.
    fingerprint = models.CharField(max_length=255)

    class Meta:
        constraints = [
            # Qayta ishga tushirilganda dublikat yaratilmaydi
            models.UniqueConstraint(fields=["fingerprint", "period_date"], name="uniq_discrepancy_per_period"),
        ]
        indexes = [models.Index(fields=["status", "type"])]


class ProviderRequestLog(models.Model):
    """Provayderga CHIQUVCHI har bir so'rov/javob (B5). Nizoda dalil."""

    provider = models.CharField(max_length=20)
    operation = models.CharField(max_length=64)
    request = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class PaymeCallbackLog(models.Model):
    """Har bir Payme webhook chaqiruvi (A8).

    Nizo chiqqanda ("pul yechildi, bron yo'q") bu yagona dalil. Retention: 1 yil.
    Faqat INSERT — yozuvlar o'zgartirilmaydi.
    """

    method = models.CharField(max_length=64, blank=True)
    params = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["method", "-created_at"])]
