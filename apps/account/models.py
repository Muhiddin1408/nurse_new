from uuid import uuid4

from django.contrib.auth.base_user import BaseUserManager, AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.utils.models import BaseModel



class UserManager(BaseUserManager):
    """Telefon raqami asosidagi manager.

    Django'ning standart manager'i `username` kutadi — bizda u yo'q.
    """

    use_in_migrations = True

    def create_user(self, phone: str, full_name: str = "", **extra):
        if not phone:
            raise ValueError("Telefon raqami majburiy")
        user = self.model(phone=phone, full_name=full_name, **extra)
        # OTP orqali kiradigan foydalanuvchida parol yo'q
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_phone_verified", True)
        extra.setdefault("role", User.Role.PLATFORM_ADMIN)

        user = self.model(phone=phone, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user



class User(AbstractBaseUser, PermissionsMixin):
    """Telefon raqami asosiy identifikator — O'zbekistonda email kam ishlatiladi.

    `AbstractBaseUser` beradi: password, last_login, set_password, check_password
    `PermissionsMixin` beradi: is_superuser, groups, user_permissions
    Ularsiz `request.user`, DRF permission'lari va Django admin ishlamaydi.
    """

    class Role(models.TextChoices):
        CLIENT = "client", "Mijoz"
        DOCTOR = "doctor", "Shifokor"
        CLINIC_ADMIN = "clinic_admin", "Klinika admini"
        PLATFORM_ADMIN = "platform_admin", "Platforma admini"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    phone = models.CharField(max_length=20, unique=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CLIENT)
    # C14: bildirishnomalar (SMS/Telegram/push) shu tilda. API javoblari tili —
    # `Accept-Language` sarlavhasidan (ilova o'zi yuboradi).
    preferred_language = models.CharField(max_length=2, choices=[("uz", "O'zbekcha"), ("ru", "Русский")],
                                          default="uz")

    is_phone_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # admin panelga kirish

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    class Meta:
        indexes = [models.Index(fields=["role", "is_active"])]

    def __str__(self):
        return f"{self.full_name or 'Nomsiz'} ({self.phone})"



class Patient(BaseModel):
    """Bemor — foydalanuvchining o'zi yoki uning oila a'zosi.

    Dizayndagi "Shag 3. Vybor patsiyenta" ekranidagi Siz / Mama / Papa aynan shu.
    Bron User'ga emas, Patient'ga bog'lanadi.
    """

    class Gender(models.TextChoices):
        MALE = "male", "Erkak"
        FEMALE = "female", "Ayol"

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="patients")
    full_name = models.CharField(max_length=255)
    relation = models.CharField(max_length=50, blank=True)  # "Onam", "Otam", "O'zim"
    birth_date = models.DateField()
    gender = models.CharField(max_length=10, choices=Gender.choices)
    weight_kg = models.PositiveSmallIntegerField(null=True, blank=True)
    # C15.2: bronlari bor bemor jismonan o'chirilmaydi (Booking.patient PROTECT) —
    # ro'yxatdan yashiriladi, tarix va hisobotlar buzilmaydi
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return self.full_name


class Address(BaseModel):
    """Uy chaqiruvi uchun manzil. Geo-qidiruvda ishlatiladi."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=100, blank=True)  # "Uy", "Ish"
    city = models.CharField(max_length=100)
    street = models.CharField(max_length=255)
    # C4: kelajakdagi marshrut optimizatsiyasi uchun (bir hududdagi uy chaqiruvlarini guruhlash)
    district = models.CharField(max_length=100, blank=True)
    entrance = models.CharField(max_length=20, blank=True)  # Podyezd
    floor = models.CharField(max_length=20, blank=True)
    apartment = models.CharField(max_length=20, blank=True)
    comment = models.TextField(blank=True)  # "Podyezd oldida it bor"
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    is_default = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)  # C15.2 — bemor bilan bir xil qoida

    class Meta:
        indexes = [models.Index(fields=["user", "is_default"])]

class OtpCode(models.Model):
    """SMS tasdiqlash kodi.

    KOD OCHIQ SAQLANMAYDI — faqat xeshi. Sabab: bazaga yoki loglarga
    kirish imkoni bo'lgan har kim boshqa odamning akkauntiga kira olmasligi
    kerak. 6 xonali kod uchun xeshlash brute-force'dan to'liq himoya qilmaydi
    (10^6 variant), lekin urinishlar soni cheklangani uchun bu yetarli —
    ikkalasi birga ishlaydi.
    """

    MAX_ATTEMPTS = 5
    TTL_MINUTES = 5

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    phone = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["phone", "is_used", "-created_at"]),
            models.Index(fields=["ip_address", "-created_at"]),
        ]

    @property
    def is_expired(self) -> bool:
        return self.expires_at < timezone.now()

class AdminTOTPDevice(models.Model):
    """A12: admin panel uchun ikkinchi omil. `manage.py admin_2fa_setup <telefon>`."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret = models.CharField(max_length=64)
    # Oxirgi ishlatilgan qadam — bitta kodni ikki marta ishlatib bo'lmaydi (replay)
    last_step = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class FavoriteDoctor(models.Model):
    """C15.6: sevimli shifokorlar — takroriy bron uchun eng qisqa yo'l."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorite_doctors")
    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "doctor"], name="uniq_favorite_doctor")]
        indexes = [models.Index(fields=["user", "-created_at"])]
