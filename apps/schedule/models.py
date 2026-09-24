from django.db import models
from django.utils import timezone

from apps.utils.models import BaseModel


# Create your models here.

class WorkingRule(BaseModel):
    """Takrorlanuvchi ish jadvali: "dushanba, 09:00–17:00, klinikada".

    Bundan aniq TimeSlot'lar generatsiya qilinadi (masalan har kecha 30 kunlik).
    """

    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, related_name="working_rules")
    clinic = models.ForeignKey(
        "catalog.Clinic", on_delete=models.CASCADE, null=True, blank=True
    )  # null = uy chaqiruvi rejimi
    # D8: shu qoidadan yaratilgan slotlar shu xonada (klinika admini biriktiradi)
    room = models.ForeignKey("catalog.Room", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    weekday = models.PositiveSmallIntegerField()  # 0=Dushanba ... 6=Yakshanba
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_minutes = models.PositiveSmallIntegerField(default=30)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["doctor", "weekday"])]


class ScheduleException(BaseModel):
    """Ta'til, bayram, kasallik — shu kunlarda slot generatsiya qilinmaydi."""

    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, related_name="exceptions")
    date = models.DateField()
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["doctor", "date"], name="uniq_doctor_exception_date")
        ]


class ClinicClosure(BaseModel):
    """D7: klinika dam olish kuni (bayram, ta'mir) — o'sha klinikadagi BARCHA
    shifokorlarning slotlari yopiladi va yangilari generatsiya qilinmaydi."""

    clinic = models.ForeignKey("catalog.Clinic", on_delete=models.CASCADE, related_name="closures")
    date = models.DateField()
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["clinic", "date"], name="uniq_clinic_closure_date")]


class TimeOff(BaseModel):
    """D3: ta'til, kasallik, dam olish — sana oralig'i YOKI bir necha soat.

    `ScheduleException` (butun kun) dan farqi: aniq vaqt oralig'i. Yaratilganda
    oraliqdagi bo'sh slotlar `BLOCKED(time_off)` bo'ladi, o'chirilganda qaytadi.
    """

    class Kind(models.TextChoices):
        VACATION = "vacation", "Ta'til"
        SICK = "sick", "Kasallik"
        PERSONAL = "personal", "Shaxsiy"
        OTHER = "other", "Boshqa"

    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, related_name="time_offs")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(end_at__gt=models.F("start_at")), name="time_off_end_after_start"),
        ]
        indexes = [models.Index(fields=["doctor", "start_at", "end_at"])]


class TimeSlot(BaseModel):
    """Aniq vaqt oralig'i. LOYIHANING ENG MUHIM MODELI.

    Bu yerdagi UNIQUE constraint butun tizimning asosiy invariantini kafolatlaydi:
    bitta shifokor bitta vaqtda faqat bitta joyda band bo'ladi — klinikada bo'ladimi,
    uy chaqiruvidami, farqi yo'q. Baza buni kod darajasida emas, o'zi qo'riqlaydi.

    Agar bu constraint bo'lmasa, ikki mijoz bir vaqtda so'rov yuborganda ikkalasi ham
    bron qilib olishi mumkin (race condition). Buni Faza 1 da test bilan isbotlaysiz.
    """

    class Status(models.TextChoices):
        FREE = "free", "Bo'sh"
        HELD = "held", "Vaqtincha band"  # to'lov kutilmoqda
        BOOKED = "booked", "Band"
        BLOCKED = "blocked", "Yopilgan"

    doctor = models.ForeignKey("catalog.Doctor", on_delete=models.CASCADE, related_name="slots")
    clinic = models.ForeignKey(
        "catalog.Clinic", on_delete=models.SET_NULL, null=True, blank=True
    )  # null = uy chaqiruvi
    room = models.ForeignKey("catalog.Room", on_delete=models.SET_NULL, null=True, blank=True, related_name="slots")  # D8
    start_at = models.DateTimeField()  # HAR DOIM UTC'da saqlanadi
    end_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.FREE)

    # HELD holati uchun: shu vaqtdan keyin avtomatik FREE ga qaytadi
    hold_expires_at = models.DateTimeField(null=True, blank=True)

    class BlockReason(models.TextChoices):
        NONE = "", "—"
        TRAVEL_BUFFER = "travel_buffer", "Uy chaqiruvi yo'l vaqti"
        TIME_OFF = "time_off", "Ta'til / dam olish"
        MANUAL = "manual", "Shifokor yopdi"
        CLINIC_CLOSED = "clinic_closed", "Klinika yopiq"

    # BLOCKED nima uchun — har bir sabab faqat O'Z blokini ochadi. Ilgari faqat
    # BLOCKED bor edi: uy chaqiruvi bekor qilinganda qo'shni ta'til slotlari ham ochilib ketardi.
    block_reason = models.CharField(max_length=20, choices=BlockReason.choices, blank=True, default="")
    time_off = models.ForeignKey(TimeOff, on_delete=models.SET_NULL, null=True, blank=True, related_name="slots")
    block_note = models.CharField(max_length=255, blank=True)
    # C4: uy chaqiruvi slotida hold paytida hisoblangan dinamik yo'l buferi.
    # `release_slot` AYNAN shu qiymat bilan ochadi — global konstanta bilan
    # ochsa, katta bufer bilan yopilgan qo'shnilar abadiy BLOCKED qolardi.
    travel_buffer_minutes = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            # ⬇⬇⬇ Butun loyihaning yuragi shu qator
            models.UniqueConstraint(fields=["doctor", "start_at"], name="uniq_doctor_slot_start"),
            # D8: bitta xonada bir vaqtda bitta qabul
            models.UniqueConstraint(fields=["room", "start_at"], condition=models.Q(room__isnull=False),
                                    name="uniq_room_slot_start"),
            models.CheckConstraint(condition=models.Q(end_at__gt=models.F("start_at")), name="slot_end_after_start"),
        ]
        indexes = [
            # "Falon mutaxassisning ertangi bo'sh vaqtlari" so'rovi uchun
            models.Index(fields=["doctor", "status", "start_at"]),
            models.Index(fields=["clinic", "status", "start_at"]),
        ]

    def __str__(self):
        return f"{self.doctor} — {self.start_at:%Y-%m-%d %H:%M} [{self.status}]"

    @property
    def is_expired_hold(self) -> bool:
        return (
                self.status == self.Status.HELD
                and self.hold_expires_at is not None
                and self.hold_expires_at < timezone.now()
        )
