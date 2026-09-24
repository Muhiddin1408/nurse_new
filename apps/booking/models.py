from django.db import models

from apps.utils.models import BaseModel


# Create your models here.

class Booking(BaseModel):
    """Bron. Faza 4 da bu saga orkestratoriga aylanadi.

    `status` maydonidagi holatlar ketma-ketligi:
        PENDING_PAYMENT → CONFIRMED → COMPLETED
                        ↘ CANCELLED / EXPIRED
    """

    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "To'lov kutilmoqda"
        CONFIRMED = "confirmed", "Tasdiqlangan"
        COMPLETED = "completed", "Yakunlangan"
        CANCELLED = "cancelled", "Bekor qilingan"
        EXPIRED = "expired", "Muddati o'tgan"
        NO_SHOW = "no_show", "Kelmadi"

    number = models.CharField(max_length=20, unique=True)  # mijozga ko'rsatiladigan raqam

    # Klient yuborgan `Idempotency-Key` headeri.
    #
    # Mobil ilova tugmani ikki marta bosishi yoki tarmoq javobni yo'qotib
    # so'rovni takrorlashi normal hol. Kalitsiz bunday holatda ikkita bron
    # yaratiladi va mijozdan ikki marta pul yechiladi.
    #
    # null=True — kalit MAJBURIY EMAS (eski klientlar yubormaydi).
    # Kalit FOYDALANUVCHIGA bog'langan (A5): UNIQUE(client, idempotency_key).
    # Global bo'lsa, begona kalit bilan boshqa odamning broni javobini olish
    # mumkin bo'lardi.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)
    # So'rov tanasining SHA-256 xeshi: bir xil kalit + boshqa tana -> 422
    request_hash = models.CharField(max_length=64, blank=True)

    client = models.ForeignKey("account.User", on_delete=models.PROTECT, related_name="bookings")
    patient = models.ForeignKey("account.Patient", on_delete=models.PROTECT)
    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.PROTECT, related_name="bookings")
    # ForeignKey, OneToOne EMAS: bekor qilingan/muddati o'tgan bron slotni
    # abadiy egallab turmasligi kerak. "Bitta slotda bitta FAOL bron"
    # invarianti pastdagi shartli UNIQUE constraint bilan kafolatlanadi.
    slot = models.ForeignKey("schedule.TimeSlot", on_delete=models.PROTECT, related_name="bookings")

    # Uy chaqiruvi bo'lsa — manzil, klinikada bo'lsa — null
    address = models.ForeignKey(
        "account.Address", on_delete=models.PROTECT, null=True, blank=True
    )

    class PaymentMode(models.TextChoices):
        PREPAID = "prepaid", "To'liq oldindan"
        DEPOSIT = "deposit", "Zaklad"
        AT_CLINIC = "at_clinic", "Klinikada to'lanadi"

    # B10: "avval to'la, 10 daqiqa" modeli O'zbekiston bozorida konversiyani
    # keskin tushiradi. Rejim shifokor sozlamasi va qabul joyidan KELIB
    # CHIQADI (mijoz tanlamaydi — aks holda u har doim eng arzon yo'lni
    # tanlar va kafolat yo'qolardi).
    payment_mode = models.CharField(
        max_length=10, choices=PaymentMode.choices, default=PaymentMode.PREPAID
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)  # chegirmadan KEYIN — mijoz to'laydi

    # C12: chegirma SNAPSHOT. `original_price - discount_amount == total_price`.
    original_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_kind = models.CharField(max_length=20, blank=True)  # promo | first_booking | follow_up | package
    promo_code = models.CharField(max_length=32, blank=True)
    # C12 dinamik narx: `original_price` ga ALLAQACHON kiritilgan tuzatma
    # (manfiy = arzonroq). Faqat shaffoflik va analitika uchun saqlanadi —
    # hisobda qayta qo'llanmaydi.
    price_adjust_percent = models.SmallIntegerField(default=0)
    # Kim to'laydi: platforma (komissiyadan) yoki shifokor (payout'dan) — B8 taqsimotiga ta'sir
    discount_borne_by = models.CharField(max_length=10, blank=True)  # platform | doctor
    # Onlayn to'lanishi kerak bo'lgan summa (SNAPSHOT): prepaid -> to'liq,
    # deposit -> foiz, at_clinic -> 0. Checkout va Payme summa tekshiruvi
    # `total_price` ni emas, AYNAN SHUNI ishlatadi.
    prepay_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # B8: pul taqsimoti SNAPSHOT (api/booking/pricing.py). Stavkalar bron
    # paytida muzlatiladi — keyin foiz o'zgarsa ham eski bronlar o'zgarmaydi.
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    provider_fee_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    platform_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    provider_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    doctor_payout = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    client_comment = models.TextField(blank=True)
    cancelled_reason = models.TextField(blank=True)

    class Actor(models.TextChoices):
        CLIENT = "client", "Mijoz"
        DOCTOR = "doctor", "Shifokor"
        ADMIN = "admin", "Admin"
        SYSTEM = "system", "Tizim"

    # D4: shifokor "qabul boshlandi" bosdi — kutish vaqti statistikasi uchun
    started_at = models.DateTimeField(null=True, blank=True)
    # C1: qabul yakunlandi — kim va qachon belgiladi
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.CharField(max_length=10, choices=Actor.choices, blank=True)
    # C1: "kelmadi" ikki xil — mijoz kelmadi (pul shifokorga) yoki shifokor
    # kelmadi (to'liq refund). Bitta holatga yig'ilmaydi.
    no_show_by = models.CharField(max_length=10, choices=Actor.choices, blank=True)
    note = models.TextField(blank=True)  # shifokor izohi / tavsiya

    # C3: necha marta ko'chirilgan. Cheklovsiz ko'chirish — slot
    # spekulyatsiyasi: bitta bron bilan bir necha vaqtni navbatma-navbat
    # egallab turish mumkin bo'lardi.
    rescheduled_count = models.PositiveSmallIntegerField(default=0)

    ACTIVE_STATUSES = ("pending_payment", "confirmed")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["slot"],
                condition=models.Q(status__in=["pending_payment", "confirmed"]),
                name="uniq_active_booking_per_slot",
            ),
            models.UniqueConstraint(
                fields=["client", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_booking_client_idempotency_key",
            ),
        ]
        indexes = [
            models.Index(fields=["client", "-created_at"]),
            models.Index(fields=["doctor", "status"]),
            models.Index(fields=["status", "created_at"]),  # muddati o'tganlarni topish uchun
        ]

    def __str__(self):
        return f"#{self.number} — {self.get_status_display()}"


class Dispute(BaseModel):
    """Nizo (B11): "to'ladim, shifokor kelmadi" va h.k. SLA — 48 soat."""

    class Reason(models.TextChoices):
        DOCTOR_NO_SHOW = "doctor_no_show", "Shifokor kelmadi"
        POOR_SERVICE = "poor_service", "Xizmat sifatsiz"
        WRONG_CHARGE = "wrong_charge", "Noto'g'ri summa yechildi"
        OTHER = "other", "Boshqa"

    class Status(models.TextChoices):
        OPEN = "open", "Ochiq"
        RESOLVED_CLIENT = "resolved_client", "Mijoz foydasiga"
        RESOLVED_DOCTOR = "resolved_doctor", "Shifokor foydasiga"

    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="disputes")
    raised_by = models.ForeignKey("account.User", on_delete=models.PROTECT, related_name="disputes")
    reason = models.CharField(max_length=30, choices=Reason.choices)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    due_at = models.DateTimeField()  # SLA
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        "account.User", on_delete=models.PROTECT, null=True, blank=True, related_name="resolved_disputes"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    refund = models.ForeignKey("payment.Refund", on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["booking"], condition=models.Q(status="open"), name="uniq_open_dispute_per_booking"
            ),
        ]
        indexes = [models.Index(fields=["status", "due_at"])]


class WaitlistEntry(BaseModel):
    """C10: band vaqt tufayli yo'qolgan talab.

    `bookings_failed{slot_taken}` metrikasi "talab > taklif" ni ko'rsatadi,
    lekin o'sha talab hech qayerda saqlanmaydi — mijoz ketadi va qaytmaydi.
    Bu jadval uni ushlab qoladi: slot bo'shaganda (bekor qilish yoki muddat
    o'tishi) navbatdagi birinchi odamga xabar ketadi.

    Bekor qilingan slotni QAYTA SOTADI — sof daromad, chunki talab uchun
    marketing puli allaqachon sarflangan.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Kutmoqda"
        NOTIFIED = "notified", "Xabar berildi"
        CONVERTED = "converted", "Bronga aylandi"
        CANCELLED = "cancelled", "Bekor qilindi"
        EXPIRED = "expired", "Muddati o'tdi"

    client = models.ForeignKey("account.User", on_delete=models.CASCADE, related_name="waitlist")
    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, related_name="waitlist")

    # Qidirilayotgan oyna va joy — bo'shagan slot shularga mos kelishi kerak
    date_from = models.DateField()
    date_to = models.DateField()
    place = models.CharField(max_length=10, choices=[("clinic", "Klinikada"), ("home", "Uyda")])

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    notified_at = models.DateTimeField(null=True, blank=True)
    notified_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            # Bitta mijoz bitta shifokorga bitta faol navbat. Aks holda
            # "har ehtimolga qarshi" o'nta yozuv qo'shiladi va bo'shagan
            # slot haqida bitta odam o'n marta SMS oladi.
            models.UniqueConstraint(
                fields=["client", "doctor"],
                condition=models.Q(status__in=["active", "notified"]),
                name="uniq_active_waitlist_per_doctor",
            ),
        ]
        indexes = [
            # Bo'shagan slot uchun navbatdagi birinchi odamni topish
            models.Index(fields=["doctor", "status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.doctor_id} — {self.date_from}..{self.date_to}"


class BookingItem(BaseModel):
    """Bir bronda bir necha xizmat bo'lishi mumkin (dizayndagi "Итого" qatori).

    Narx SNAPSHOT sifatida saqlanadi — keyinchalik xizmat narxi o'zgarsa,
    eski bronlar o'zgarmasligi kerak. Bu moliyaviy ma'lumot bilan ishlashning
    asosiy qoidasi.
    """

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey("catalog.Service", on_delete=models.PROTECT)
    service_name = models.CharField(max_length=255)  # snapshot
    price = models.DecimalField(max_digits=12, decimal_places=2)  # snapshot
    duration_minutes = models.PositiveSmallIntegerField()  # snapshot
    # B12: fiskal rekvizitlar ham SNAPSHOT — chek bron paytidagi kod bilan chiqadi
    mxik_code = models.CharField(max_length=32, blank=True)
    package_code = models.CharField(max_length=32, blank=True)
    vat_percent = models.PositiveSmallIntegerField(default=0)



class Review(BaseModel):
    """C11: sharh. Faqat `completed` bronga — sharh yozish uchun avval to'lash
    va qabulga borish kerak, bu soxta sharhga qarshi tabiiy himoya.

    Bitta bronga bitta sharh (OneToOne). Reyting `Doctor.rating` ga faqat
    `published` sharhlardan hisoblanadi.
    """

    class Status(models.TextChoices):
        PUBLISHED = "published", "E'lon qilingan"
        PENDING = "pending", "Moderatsiyada"  # avtomatik filtr shubha qildi
        REJECTED = "rejected", "Rad etilgan"

    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="review")
    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.PROTECT, related_name="reviews")
    client = models.ForeignKey("account.User", on_delete=models.PROTECT, related_name="reviews")
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    is_anonymous = models.BooleanField(default=False)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PUBLISHED)
    # Avtomatik filtr nima topdi — moderator nima uchun ko'rayotganini bilishi kerak
    moderation_flags = models.JSONField(default=list, blank=True)
    moderated_by = models.ForeignKey(
        "account.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="moderated_reviews"
    )
    moderated_at = models.DateTimeField(null=True, blank=True)

    # Shifokor javobi — bir marta
    doctor_reply = models.TextField(blank=True)
    doctor_replied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rating__gte=1, rating__lte=5), name="review_rating_1_5"
            ),
        ]
        indexes = [
            models.Index(fields=["doctor", "status", "-created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]


class PromoCode(BaseModel):
    """C12: promo kod. Marketing kampaniyasi yoki kompensatsiya (C2)."""

    class Kind(models.TextChoices):
        PERCENT = "percent", "Foiz"
        FIXED = "fixed", "Qat'iy summa"

    class BorneBy(models.TextChoices):
        PLATFORM = "platform", "Platforma"
        DOCTOR = "doctor", "Shifokor"

    code = models.CharField(max_length=32, unique=True)  # har doim KATTA harf
    kind = models.CharField(max_length=10, choices=Kind.choices)
    value = models.DecimalField(max_digits=12, decimal_places=2)  # foiz (1–100) yoki so'm
    max_discount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)       # jami
    max_uses_per_user = models.PositiveIntegerField(default=1)
    first_booking_only = models.BooleanField(default=False)
    # Faqat shu shifokor uchun (shifokor o'z aksiyasi) — bo'sh = hamma
    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    borne_by = models.CharField(max_length=10, choices=BorneBy.choices, default=BorneBy.PLATFORM)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class PromoRedemption(BaseModel):
    """Promo ishlatilishi. Bron bekor / muddati o'tsa — `is_active=False` (limit qaytadi)."""

    promo = models.ForeignKey(PromoCode, on_delete=models.PROTECT, related_name="redemptions")
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="promo_redemption")
    client = models.ForeignKey("account.User", on_delete=models.CASCADE, related_name="+")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["promo", "client", "is_active"])]


class PackageEnrollment(BaseModel):
    """C12: mijozning faol paketi (kursi). `catalog.ServicePackage` nusxasi
    ustidan SNAPSHOT — shifokor ertaga paketni o'chirsa yoki foizini
    o'zgartirsa, boshlangan kurs o'z shartida qoladi."""

    package = models.ForeignKey("catalog.ServicePackage", on_delete=models.PROTECT, related_name="enrollments")
    client = models.ForeignKey("account.User", on_delete=models.CASCADE, related_name="package_enrollments")
    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, related_name="+")
    service = models.ForeignKey("catalog.Service", on_delete=models.CASCADE, related_name="+")

    sessions_total = models.PositiveSmallIntegerField()
    discount_percent = models.PositiveSmallIntegerField()
    expires_at = models.DateTimeField(db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["client", "service", "is_active"])]
        constraints = [
            # Bir xizmat bo'yicha bir vaqtda bitta faol kurs — aks holda
            # qaysi biridan seans yechilgani noaniq bo'lib qoladi.
            models.UniqueConstraint(
                fields=["client", "service"], condition=models.Q(is_active=True),
                name="uniq_active_package_per_service",
            )
        ]

    @property
    def sessions_used(self) -> int:
        return self.uses.filter(is_active=True).count()

    @property
    def sessions_left(self) -> int:
        return max(self.sessions_total - self.sessions_used, 0)


class PackageUse(BaseModel):
    """Kursdan bitta seans yechilishi. `PromoRedemption` bilan bir xil mantiq:
    bron bekor bo'lsa `is_active=False` — seans kursga QAYTADI."""

    enrollment = models.ForeignKey(PackageEnrollment, on_delete=models.CASCADE, related_name="uses")
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="package_use")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["enrollment", "is_active"])]
