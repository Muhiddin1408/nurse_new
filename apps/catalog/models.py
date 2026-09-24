from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.account.models import User
from apps.catalog.storage import private_storage
from apps.utils.models import BaseModel


class Clinic(BaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Qoralama"
        PENDING = "pending", "Moderatsiyada"
        ACTIVE = "active", "Faol"
        SUSPENDED = "suspended", "To'xtatilgan"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    phone = models.CharField(max_length=20)
    city = models.CharField(max_length=100)
    street = models.CharField(max_length=255)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    reviews_count = models.PositiveIntegerField(default=0)
    # D7: ish vaqti — {"0": ["08:00", "18:00"], ..., "6": null}; 0 = dushanba, null = dam
    working_hours = models.JSONField(default=dict, blank=True)
    photo_url = models.URLField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "city"]),
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return self.name


class Specialization(BaseModel):
    """Gastroenterolog, Terapevt, LOR va h.k."""

    name = models.CharField(max_length=100, unique=True)
    name_ru = models.CharField(max_length=100, blank=True)  # C14
    slug = models.SlugField(unique=True)
    is_pediatric = models.BooleanField(default=False)  # "Bolalar" filtri uchun

    # C6: bemor ↔ mutaxassislik mosligi. Kattalar shifokori default
    # bolalarni QABUL QILMAYDI — 3 yoshli bola terapevtga yozilsa, shifokor
    # qaytaradi, mijoz esa vaqt va yo'l sarflagan bo'ladi.
    accepts_children = models.BooleanField(default=False)
    min_patient_age = models.PositiveSmallIntegerField(null=True, blank=True)
    max_patient_age = models.PositiveSmallIntegerField(null=True, blank=True)
    # Bo'sh — cheklov yo'q; "female" — masalan ginekolog (sozlanadigan)
    allowed_gender = models.CharField(
        max_length=10, blank=True, choices=[("", "Hammasi"), ("male", "Erkak"), ("female", "Ayol")]
    )

    def __str__(self):
        return self.name


class Doctor(BaseModel):
    """Shifokor.

    MUHIM: shifokor klinikada ishlashi HAM, mustaqil uy chaqiruvi qabul qilishi HAM
    mumkin. Shuning uchun Doctor Clinic'ga to'g'ridan-to'g'ri bog'lanmaydi —
    bog'lanish DoctorAffiliation orqali va u ixtiyoriy.
    """

    class Status(models.TextChoices):
        """D2 holat mashinasi:
            draft -> pending(moderatsiya) -> approved | rejected -> (tuzatib) -> pending
            approved -> suspended -> approved
        """

        DRAFT = "draft", "Qoralama"
        PENDING = "pending", "Moderatsiyada"
        APPROVED = "approved", "Tasdiqlangan"
        REJECTED = "rejected", "Rad etilgan"
        SUSPENDED = "suspended", "To'xtatilgan"

    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="doctor_profile")
    specializations = models.ManyToManyField(Specialization, related_name="doctors")
    bio = models.TextField(blank=True)
    experience_years = models.PositiveSmallIntegerField(default=0)
    education = models.TextField(blank=True)
    languages = models.JSONField(default=list, blank=True)  # ["uz", "ru"]
    license_number = models.CharField(max_length=100, blank=True)
    license_expires_at = models.DateField(null=True, blank=True)
    license_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Moderatsiya izi
    submitted_at = models.DateTimeField(null=True, blank=True)
    moderated_at = models.DateTimeField(null=True, blank=True)
    moderated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="moderated_doctors"
    )
    status_reason = models.TextField(blank=True)  # rad etish / to'xtatish sababi

    # D11: Telegram bot bilan bog'langan chat (kunlik qabullar, bildirishnomalar)
    telegram_chat_id = models.BigIntegerField(null=True, blank=True, unique=True)

    # B10: klinikadagi qabul uchun to'lov rejimi. Uy chaqiruvi bu
    # sozlamaga BO'YSUNMAYDI — shifokor yo'lga chiqadi, kafolat shart.
    #
    # Default `prepaid`: yangi shifokor uchun eng xavfsiz holat. Rejimni
    # o'zgartirish — biznes qarori (no-show riskini shifokor oladi,
    # konversiya esa 2–3 barobar oshadi).
    clinic_payment_mode = models.CharField(
        max_length=10,
        choices=[("prepaid", "To'liq oldindan"), ("deposit", "Zaklad"), ("at_clinic", "Klinikada")],
        default="prepaid",
    )

    # C12: 14 kun ichida o'sha shifokorga takroriy qabul chegirmasi (0 = yo'q,
    # 100 = bepul). Shifokor o'zi belgilaydi va o'zi to'laydi (payout'dan).
    follow_up_discount_percent = models.PositiveSmallIntegerField(default=0)

    # Uy chaqiruvi imkoniyati
    accepts_home_visits = models.BooleanField(default=False)
    home_visit_radius_km = models.PositiveSmallIntegerField(default=10)
    # C4: radius shu nuqtadan o'lchanadi. Bo'sh bo'lsa radius tekshirilmaydi
    # (eski profillar) — shifokor profilida to'ldirilishi kerak.
    home_base_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    home_base_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    reviews_count = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [
            models.Index(fields=["status", "accepts_home_visits"]),
        ]

    def __str__(self):
        return self.user.full_name


def _doctor_document_path(instance, filename):
    # Asl fayl nomi yo'lga tushmaydi (PII, path traversal) — faqat UUID
    import uuid
    from pathlib import Path

    return f"doctor_documents/{instance.doctor_id}/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class DoctorDocument(BaseModel):
    """D2: diplom, litsenziya, sertifikat, pasport.

    HECH QACHON OCHIQ EMAS: fayl `PRIVATE_MEDIA_ROOT` da (MEDIA_URL orqali
    berilmaydi) va faqat qisqa muddatli imzolangan havola orqali yuklab olinadi.
    """

    class Kind(models.TextChoices):
        DIPLOMA = "diploma", "Diplom"
        LICENSE = "license", "Litsenziya"
        CERTIFICATE = "certificate", "Sertifikat"
        PASSPORT = "passport", "Pasport"

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(upload_to=_doctor_document_path, storage=private_storage)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()


class ClinicMembership(BaseModel):
    """D1: foydalanuvchi — klinika admini (yoki reception). Rol shu jadvaldan hisoblanadi."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="clinic_memberships")
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="memberships")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "clinic"], name="uniq_clinic_member")]


class DoctorAffiliation(BaseModel):
    """Shifokor ↔ Klinika bog'lanishi. Bir shifokor bir necha klinikada ishlashi mumkin."""

    class Status(models.TextChoices):
        """D7: IKKI TOMONLAMA tasdiq. Klinika taklif qiladi -> shifokor qabul qiladi.
        Aks holda klinika istalgan shifokorni o'ziga "yozib" olib, uning nomi bilan
        xizmat sotishi mumkin edi.

            invited -> active | declined
            active <-> paused (klinika vaqtincha to'xtatadi)
            active | paused -> ended (shifokor ketdi yoki klinika uzdi)
        """

        INVITED = "invited", "Taklif yuborilgan"
        ACTIVE = "active", "Faol"
        PAUSED = "paused", "To'xtatilgan"
        DECLINED = "declined", "Rad etilgan"
        ENDED = "ended", "Tugatilgan"

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="affiliations")
    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="affiliations")
    position = models.CharField(max_length=100, blank=True)
    # `is_active` == (status == active). Butun kod bazasi shu maydon bo'yicha
    # filtrlaydi — u saqlanadi, `status` bilan birga yangilanadi.
    is_active = models.BooleanField(default=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["doctor", "clinic"], name="uniq_doctor_clinic")
        ]


class Service(BaseModel):
    """Xizmat: konsultatsiya, ukol, UZI...

    Narx va davomiylik shu yerda — bron qilishda slot uzunligi shundan olinadi.
    Xizmat klinikaga TEGISHLI bo'lishi ham, shifokorning shaxsiy xizmati bo'lishi ham
    mumkin (uy chaqiruvi uchun). Shuning uchun ikkala FK ham nullable.
    """

    class Place(models.TextChoices):
        CLINIC = "clinic", "Klinikada"
        HOME = "home", "Uyda"

    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="services", null=True, blank=True
    )
    doctor = models.ForeignKey(
        Doctor, on_delete=models.CASCADE, related_name="services", null=True, blank=True
    )
    specialization = models.ForeignKey(Specialization, on_delete=models.PROTECT)

    name = models.CharField(max_length=255)
    name_ru = models.CharField(max_length=255, blank=True)  # C14 — bo'sh bo'lsa `name`
    description = models.TextField(blank=True)
    place = models.CharField(max_length=10, choices=Place.choices)
    duration_minutes = models.PositiveSmallIntegerField(validators=[MinValueValidator(5)])
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)

    # B12: fiskal chek. MXIK (IKPU) — soliq klassifikatori kodi, `package_code` —
    # o'lchov birligi kodi. Bo'sh bo'lsa `settings.FISCAL` dagi default ishlatiladi.
    mxik_code = models.CharField(max_length=32, blank=True)
    package_code = models.CharField(max_length=32, blank=True)
    vat_percent = models.PositiveSmallIntegerField(null=True, blank=True)

    def __str__(self):
        return self.name

    class Meta:
        constraints = [
            # Xizmat yo klinikaniki, yo shifokorniki — ikkalasi ham bo'sh bo'lolmaydi
            models.CheckConstraint(
                condition=models.Q(clinic__isnull=False) | models.Q(doctor__isnull=False),
                name="service_has_owner",
            )
        ]
        indexes = [models.Index(fields=["specialization", "place", "is_active"])]


class ServicePriceHistory(models.Model):
    """D5: narx o'zgarishi tarixi — analitika va nizolar uchun. Faqat INSERT."""

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="price_history")
    old_price = models.DecimalField(max_digits=12, decimal_places=2)
    new_price = models.DecimalField(max_digits=12, decimal_places=2)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    is_large_change = models.BooleanField(default=False)  # > 50% oshish
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class Room(BaseModel):
    """D8: klinika xonasi (kabinet) — cheklangan resurs. 10 shifokor, 4 xona:
    `UNIQUE(doctor, start_at)` yetmaydi, `UNIQUE(room, start_at)` ham kerak."""

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=100)
    floor = models.CharField(max_length=20, blank=True)
    equipment = models.JSONField(default=list, blank=True)  # ["UZI", "EKG"]
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["clinic", "name"], name="uniq_room_name_per_clinic")]

    def __str__(self):
        return f"{self.clinic} — {self.name}"


class PriceRule(BaseModel):
    """C12: dinamik narx. Kam talab vaqtlarida slot arzonlashadi.

    Qoida SLOT BOSHLANISH VAQTIGA (mahalliy vaqt zonasi) qaraydi, bron
    qilingan vaqtga emas: mijoz kechqurun bron qilsa ham, ertalabki 08:00
    sloti o'sha ertalabki narxda qoladi.

    `percent` manfiy = arzonlashtirish (-15 → narx 15% past), musbat =
    qimmatlashtirish (kech soatlar uchun ustama). Bu CHEGIRMA EMAS: u
    `Booking.original_price` ning o'ziga kiradi va `BookingItem.price`
    snapshot'ida muzlaydi, shuning uchun promo/paket chegirmasi bilan
    RAQOBATLASHMAYDI — avval narx tuzatiladi, keyin chegirma qo'llanadi.

    Bir vaqtga bir nechta qoida to'g'ri kelsa — `priority` eng kichigi.
    """

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="price_rules")
    name = models.CharField(max_length=100, blank=True)  # "Ertalabki aksiya"
    # Dushanba=0 ... Yakshanba=6. Bo'sh = har kuni.
    weekdays = models.CharField(max_length=7, blank=True)
    start_time = models.TimeField()
    end_time = models.TimeField()  # [start_time, end_time) — chegara kirmaydi
    percent = models.SmallIntegerField(
        validators=[MinValueValidator(-90), MaxValueValidator(100)]
    )
    priority = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["doctor", "is_active", "priority"])]

    def matches(self, local_dt) -> bool:
        if self.weekdays and str(local_dt.weekday()) not in self.weekdays:
            return False
        t = local_dt.time()
        if self.start_time <= self.end_time:
            return self.start_time <= t < self.end_time
        # Yarim tundan o'tadigan oraliq (22:00–06:00)
        return t >= self.start_time or t < self.end_time

    def __str__(self):
        return f"{self.name or self.doctor} {self.start_time}–{self.end_time} {self.percent:+d}%"


class ServicePackage(BaseModel):
    """C12: paket — "5 ta qabul, 10% chegirma". Surunkali kasallikda standart.

    Mijoz OLDINDAN TO'LAMAYDI: paketga yoziladi (`PackageEnrollment`) va
    keyingi `sessions` ta broni chegirmali bo'ladi. Sababi — `Payment` shu
    tizimda bronga bog'langan (OneToOne); paketni alohida sotish Payme/Click
    protokolining o'ziga tegadi va qaytarish (refund) mantig'ini ikkiga
    bo'ladi. Foydalanilmagan seans — qaytariladigan pul emas, shunchaki
    ishlatilmagan huquq; shuning uchun bu model xavfsiz va teskari.

    Chegirmani shifokor ko'taradi (`borne_by=doctor`) — bu uning kurs
    taklifi, platforma marketingi emas.
    """

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="packages")
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="packages")
    name = models.CharField(max_length=120)
    sessions = models.PositiveSmallIntegerField(validators=[MinValueValidator(2)])
    discount_percent = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(90)])
    validity_days = models.PositiveSmallIntegerField(default=90)
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=["doctor", "is_active"])]

    def __str__(self):
        return f"{self.name} ({self.sessions}×, -{self.discount_percent}%)"
