"""
schedule/services.py — Faza 1

Jadval generatsiyasi va bandlikni boshqarish.

MODUL CHEGARASI QOIDASI:
    Bu fayl catalog yoki booking modellarini IMPORT QILMAYDI.
    Barcha funksiyalar `doctor_id` (UUID) qabul qiladi, `Doctor` obyektini emas.
    Shu tufayli Faza 2-4 da bu modulni alohida servisga ko'chirish mexanik ish bo'ladi:
    faqat funksiya chaqiruvlari tarmoq chaqiruviga aylanadi, logika o'zgarmaydi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone  # django.utils.timezone.utc Django 5 da olib tashlangan
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.schedule.models import ClinicClosure, ScheduleException, TimeOff, TimeSlot, WorkingRule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sozlamalar
# ---------------------------------------------------------------------------

LOCAL_TZ = ZoneInfo("Asia/Tashkent")

# To'lov kutish muddati. Shu vaqt ichida to'lanmasa, slot bo'shaydi.
HOLD_TTL_MINUTES = 10

# Necha kun oldinga slot generatsiya qilinadi
GENERATION_HORIZON_DAYS = 30

# Uy chaqiruvi uchun yo'l vaqti buferi — masofa noma'lum bo'lganda (default).
# C4: masofa ma'lum bo'lsa `travel_buffer_minutes(km)` ishlatiladi.
HOME_VISIT_BUFFER_MINUTES = 30
TRAVEL_BUFFER_BASE_MINUTES = 10
TRAVEL_BUFFER_PER_KM_MINUTES = 3
TRAVEL_BUFFER_MIN_MINUTES = 15
TRAVEL_BUFFER_MAX_MINUTES = 90


def travel_buffer_minutes(distance_km: float | None) -> int:
    """C4 dinamik bufer: `10 daq + masofa × 3 daq/km`, [15, 90] oralig'ida.

    Toshkentda 2 km ga qat'iy 30 daqiqa ko'p (shifokor kuniga 1–2 qabul
    yo'qotadi), 20 km ga esa kam (keyingi bemor kutib qoladi)."""
    if distance_km is None:
        return HOME_VISIT_BUFFER_MINUTES
    minutes = TRAVEL_BUFFER_BASE_MINUTES + distance_km * TRAVEL_BUFFER_PER_KM_MINUTES
    return int(min(max(round(minutes), TRAVEL_BUFFER_MIN_MINUTES), TRAVEL_BUFFER_MAX_MINUTES))

# Eng yaqin bron necha daqiqadan keyin bo'lishi mumkin
MIN_LEAD_TIME_MINUTES = 60


class SlotNotAvailable(Exception):
    """Slot band, yopilgan yoki mavjud emas."""


@dataclass(frozen=True)
class SlotDTO:
    """Bron validatsiyasi uchun slot surati — `TimeSlot` obyekti tashqariga chiqmaydi."""

    id: UUID
    doctor_id: UUID
    clinic_id: UUID | None
    start_at: datetime
    end_at: datetime
    status: str
    hold_expires_at: datetime | None

    @property
    def duration_minutes(self) -> int:
        return int((self.end_at - self.start_at).total_seconds() // 60)

    @property
    def is_home_visit(self) -> bool:
        return self.clinic_id is None


def _free_or_expired_hold(now: datetime) -> Q:
    """LAZY EXPIRY (E8): slot bo'sh hisoblanadi, agar FREE yoki hold muddati o'tgan bo'lsa.

    Slotning to'g'riligi `expire_bookings` cron'iga tayanmaydi — job o'chib
    qolsa ham muddati o'tgan hold o'qishda ham, band qilishda ham bo'sh
    ko'rinadi. Job faqat yon ta'sirlar (bron holati, SMS) uchun qoladi.
    """
    return Q(status=TimeSlot.Status.FREE) | Q(
        status=TimeSlot.Status.HELD, hold_expires_at__lt=now
    )


def get_slot_for_booking(slot_id: UUID) -> SlotDTO | None:
    slot = TimeSlot.objects.filter(id=slot_id).first()
    if slot is None:
        return None
    return SlotDTO(
        id=slot.id,
        doctor_id=slot.doctor_id,
        clinic_id=slot.clinic_id,
        start_at=slot.start_at,
        end_at=slot.end_at,
        status=slot.status,
        hold_expires_at=slot.hold_expires_at,
    )


def is_hold_active(slot_id: UUID) -> bool:
    """Slot hali HELD va muddati o'tmaganmi — to'lov tekshiruvi (B3) uchun."""
    return TimeSlot.objects.filter(
        id=slot_id, status=TimeSlot.Status.HELD, hold_expires_at__gte=timezone.now()
    ).exists()


def extend_hold(slot_id: UUID, minutes: int) -> bool:
    """Hold'ni kamida `now + minutes` gacha uzaytiradi (B3: to'lov ochilganda).

    Faqat HELD slotda ishlaydi. Qisqartirmaydi: joriy muddat uzunroq bo'lsa
    o'zgarmaydi. Qaytaradi: slot HELD holatdami.
    """
    target = timezone.now() + timedelta(minutes=minutes)
    TimeSlot.objects.filter(
        id=slot_id, status=TimeSlot.Status.HELD, hold_expires_at__lt=target
    ).update(hold_expires_at=target, updated_at=timezone.now())
    return TimeSlot.objects.filter(id=slot_id, status=TimeSlot.Status.HELD).exists()


# ---------------------------------------------------------------------------
# 1. Slot generatsiyasi
# ---------------------------------------------------------------------------


def generate_slots(doctor_id: UUID, days: int = GENERATION_HORIZON_DAYS) -> int:
    """WorkingRule'lardan aniq TimeSlot qatorlarini yaratadi.

    Bu funksiya IDEMPOTENT — istalgan marta qayta chaqirsangiz, dublikat yaratmaydi.
    Buni `ignore_conflicts=True` va `uniq_doctor_slot_start` constraint'i ta'minlaydi.
    Idempotentlik muhim, chunki bu funksiya har kecha cron orqali ishlaydi va
    ba'zan ikki marta ishga tushib ketadi.

    Qaytaradi: yaratilgan yangi slotlar soni.
    """
    today = timezone.now().astimezone(LOCAL_TZ).date()
    horizon = today + timedelta(days=days)

    rules = WorkingRule.objects.filter(
        doctor_id=doctor_id,
        valid_from__lte=horizon,
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))

    if not rules:
        return 0

    # Ta'til/bayram kunlari — bu kunlarga slot yaratilmaydi
    exception_dates = set(
        ScheduleException.objects.filter(
            doctor_id=doctor_id, date__gte=today, date__lte=horizon
        ).values_list("date", flat=True)
    )

    # D7: klinika dam olish kunlari — o'sha klinikadagi qoidalar uchun
    closures = set(
        ClinicClosure.objects.filter(
            clinic_id__in={r.clinic_id for r in rules if r.clinic_id}, date__gte=today, date__lte=horizon
        ).values_list("clinic_id", "date")
    )

    new_slots: list[TimeSlot] = []

    for rule in rules:
        for day in _iter_dates(today, horizon):
            if day.weekday() != rule.weekday:
                continue
            if day in exception_dates:
                continue
            if rule.clinic_id and (rule.clinic_id, day) in closures:
                continue
            if day < rule.valid_from:
                continue
            if rule.valid_to and day > rule.valid_to:
                continue

            new_slots.extend(_build_slots_for_day(rule, day))

    # ⬇ D3: ta'til oralig'iga va MAVJUD slotlar bilan ustma-ust tushadigan
    # slotlar yaratilmaydi. Qoida o'zgarganda (masalan 30 -> 20 daqiqa) eski
    # band slot 10:00–10:30 turgan joyga yangi 10:20 slot qo'yilsa, shifokor
    # bir vaqtda ikki bemorga yozilib qolardi — UNIQUE(doctor, start_at) buni
    # ushlamaydi, chunki boshlanish vaqtlari farq qiladi.
    horizon_start = datetime.combine(today, time.min, tzinfo=LOCAL_TZ)
    horizon_end = datetime.combine(horizon + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    busy = sorted(
        list(TimeSlot.objects.filter(doctor_id=doctor_id, start_at__lt=horizon_end, end_at__gt=horizon_start)
             .values_list("start_at", "end_at"))
        + list(TimeOff.objects.filter(doctor_id=doctor_id, start_at__lt=horizon_end, end_at__gt=horizon_start)
               .values_list("start_at", "end_at"))
    )
    existing_starts = set(
        TimeSlot.objects.filter(doctor_id=doctor_id, start_at__gte=horizon_start, start_at__lt=horizon_end)
        .values_list("start_at", flat=True)
    )
    new_slots = [
        s for s in new_slots
        if s.start_at in existing_starts or not _overlaps_any(s.start_at, s.end_at, busy)
    ]

    # D8: xona boshqa shifokor slotida band bo'lsa — bu slot yaratilmaydi
    # (UNIQUE(room, start_at) faqat bir xil boshlanishni ushlaydi, ustma-ustlikni emas)
    room_ids = {s.room_id for s in new_slots if s.room_id}
    if room_ids:
        room_busy: dict = {}
        for room_id, st, en in (
            TimeSlot.objects.filter(room_id__in=room_ids, start_at__lt=horizon_end, end_at__gt=horizon_start)
            .exclude(doctor_id=doctor_id)
            .values_list("room_id", "start_at", "end_at")
        ):
            room_busy.setdefault(room_id, []).append((st, en))
        for intervals in room_busy.values():
            intervals.sort()
        new_slots = [
            s for s in new_slots
            if not s.room_id or not _overlaps_any(s.start_at, s.end_at, room_busy.get(s.room_id, []))
        ]

    if not new_slots:
        return 0

    created = TimeSlot.objects.bulk_create(new_slots, ignore_conflicts=True, batch_size=500)

    # DIQQAT: ignore_conflicts bilan bulk_create PostgreSQL'da haqiqatan
    # yaratilganlar sonini ishonchli qaytarmaydi. Aniq son kerak bo'lsa,
    # oldin/keyin count() qiling. Bu yerda taxminiy son yetarli.
    logger.info("Slot generatsiya: doctor=%s, urinish=%s", doctor_id, len(new_slots))
    return len(created)


def generate_slots_for_all_doctors(days: int = GENERATION_HORIZON_DAYS) -> tuple[int, int]:
    """Amaldagi WorkingRule'i bor BARCHA shifokorlar uchun `generate_slots`.

    Shifokorlar ro'yxati `catalog` dan emas, `WorkingRule` ning o'zidan olinadi —
    modul chegarasi buzilmaydi, va qoidasi yo'q shifokorga bekorga murojaat qilinmaydi.

    Bitta shifokorda xato bo'lsa, qolganlari to'xtab qolmaydi (xato logga yoziladi).

    Qaytaradi: (shifokorlar soni, yaratilgan slotlar soni).
    """
    today = timezone.now().astimezone(LOCAL_TZ).date()
    doctor_ids = (
        WorkingRule.objects.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .values_list("doctor_id", flat=True)
        .distinct()
    )

    doctors = 0
    total = 0
    for doctor_id in list(doctor_ids):
        try:
            total += generate_slots(doctor_id, days=days)
            doctors += 1
        except Exception:  # noqa: BLE001
            logger.exception("Slot generatsiyasida xato: doctor=%s", doctor_id)
    return doctors, total


def _overlaps_any(start: datetime, end: datetime, intervals: list[tuple[datetime, datetime]]) -> bool:
    import bisect

    # intervals boshlanish bo'yicha tartiblangan; faqat end dan oldin boshlanganlarni ko'ramiz
    idx = bisect.bisect_left(intervals, (end,))
    return any(i_end > start for _, i_end in intervals[:idx])


def _iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _build_slots_for_day(rule: WorkingRule, day: date) -> list[TimeSlot]:
    """Bitta qoidadan bitta kun uchun slotlar ro'yxatini quradi.

    VAQT MINTAQASI: qoida MAHALLIY vaqtda yozilgan ("09:00 da ishni boshlayman"),
    lekin bazaga UTC'da saqlanadi. Konvertatsiyani shu yerda, bitta joyda qilamiz.

    O'zbekistonda yozgi vaqtga o'tish yo'q, shuning uchun bu sodda. Agar loyiha
    boshqa mamlakatga chiqsa, DST o'tish kunlarida bir kunda 23 yoki 25 soat
    bo'lishini hisobga olish kerak bo'ladi — shuning uchun ham naive datetime
    ishlatmaymiz.
    """
    slots: list[TimeSlot] = []

    cursor = datetime.combine(day, rule.start_time, tzinfo=LOCAL_TZ)
    day_end = datetime.combine(day, rule.end_time, tzinfo=LOCAL_TZ)
    step = timedelta(minutes=rule.slot_minutes)

    while cursor + step <= day_end:
        slots.append(
            TimeSlot(
                doctor_id=rule.doctor_id,
                clinic_id=rule.clinic_id,  # None => uy chaqiruvi rejimi
                room_id=rule.room_id,  # D8
                start_at=cursor.astimezone(dt_timezone.utc),
                end_at=(cursor + step).astimezone(dt_timezone.utc),
                status=TimeSlot.Status.FREE,
            )
        )
        cursor += step

    return slots


# ---------------------------------------------------------------------------
# 2. Bo'sh vaqtlarni o'qish
# ---------------------------------------------------------------------------


def get_available_slots(
    *,
    doctor_id: UUID | None = None,
    clinic_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> QuerySet[TimeSlot]:
    """Bo'sh slotlarni qaytaradi.

    Bu tizimdagi ENG KO'P chaqiriladigan so'rov — dizayndagi har bir shifokor
    kartochkasi ostida bo'sh vaqtlar ko'rsatiladi. Faza 6 da aynan shu funksiya
    keshlanadi va 10 000 rps ga optimallashtiriladi.

    Hozircha oddiy qoldiring. Avval ishlasin, keyin o'lchaysiz.
    """
    now = timezone.now()
    earliest = now + timedelta(minutes=MIN_LEAD_TIME_MINUTES)

    qs = TimeSlot.objects.filter(
        _free_or_expired_hold(now),
        start_at__gte=max(earliest, date_from or earliest),
    )

    if doctor_id:
        qs = qs.filter(doctor_id=doctor_id)
    if clinic_id:
        qs = qs.filter(clinic_id=clinic_id)
    if date_to:
        qs = qs.filter(start_at__lte=date_to)

    return qs.order_by("start_at")


# ---------------------------------------------------------------------------
# 3. Bandlik — eng muhim qism
# ---------------------------------------------------------------------------


def get_slot_clinic_id(slot_id: UUID) -> UUID | None:
    """Slot qaysi klinikada — yoki `None` (uy chaqiruvi).

    Nega bu kerak: qabul QAYERDA bo'lishini `Service.clinic` emas, aynan
    SLOT belgilaydi. `Service.clinic` — xizmat kimga tegishli (egalik),
    `TimeSlot.clinic` — shifokor o'sha vaqtda qayerda (joylashuv). Shifokor
    bir necha klinikada ishlashi mumkin, shuning uchun ular boshqa narsa.

    UUID qaytaradi, `Clinic` obyektini emas — modul chegarasi qoidasi.
    """
    return (
        TimeSlot.objects.filter(id=slot_id)
        .values_list("clinic_id", flat=True)
        .first()
    )


def today_local() -> date:
    """Bugungi sana MAHALLIY vaqtda (`timezone.now().date()` emas!).

    `timezone.now()` UTC qaytaradi, ya'ni Toshkentda soat 02:00 bo'lganda
    `.date()` KECHAGI sanani beradi (UTC+5). Natijada tunda ochilgan
    shifokor kartochkasi kechagi kunning slotlarini so'ragan bo'lardi —
    ya'ni bo'sh ro'yxat.
    """
    return timezone.now().astimezone(LOCAL_TZ).date()


def get_slot_day(slot_id: UUID) -> date | None:
    """Slot qaysi MAHALLIY kunga tegishli — yoki `None` (slot topilmadi).

    Kesh kaliti kun bo'yicha quriladi (`slots:v1:{doctor}:{kun}`), shuning
    uchun invalidatsiya qiluvchi tomon ham AYNAN shu kunni bilishi kerak.

    Kun `LOCAL_TZ` da hisoblanadi, UTC'da emas. Bu muhim: soat 03:00 dagi
    Toshkent sloti UTC'da BIR KUN OLDIN turadi (UTC+5). UTC sanasi olinsa,
    tunggi slotlar noto'g'ri kalitga tushib, kesh o'chirilmay qolardi.

    UUID emas, `date` qaytaradi — modul chegarasi qoidasi (`TimeSlot`
    obyektini tashqariga chiqarmaymiz).
    """
    start_at = (
        TimeSlot.objects.filter(id=slot_id).values_list("start_at", flat=True).first()
    )
    if start_at is None:
        return None
    return start_at.astimezone(LOCAL_TZ).date()


def hold_slot(
    slot_id: UUID,
    ttl_minutes: int = HOLD_TTL_MINUTES,
    *,
    doctor_id: UUID | None = None,
    buffer_minutes: int | None = None,
) -> TimeSlot:
    """Slotni vaqtincha band qiladi (FREE -> HELD).

    `buffer_minutes` — uy chaqiruvi uchun yo'l buferi (C4). Berilmasa default.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  BU FUNKSIYA LOYIHADAGI ENG MUHIM 10 QATOR.                          ║
    ║                                                                       ║
    ║  NOTO'G'RI usul:                                                      ║
    ║      slot = TimeSlot.objects.get(id=slot_id)                          ║
    ║      if slot.status != FREE:      # <- shu yerda boshqa so'rov        ║
    ║          raise SlotNotAvailable    #    oraga tushib ketadi           ║
    ║      slot.status = HELD                                               ║
    ║      slot.save()                                                      ║
    ║                                                                       ║
    ║  Bu klassik race condition: ikki so'rov ham `if` dan o'tib ketadi.    ║
    ║  Lokal kompyuterda hech qachon ko'rinmaydi, prodda ko'rinadi.         ║
    ║                                                                       ║
    ║  TO'G'RI usul — compare-and-set: shartni UPDATE ning O'ZIGA qo'yish.  ║
    ║  Baza `UPDATE ... WHERE status='free'` ni atomik bajaradi, shuning    ║
    ║  uchun 100 ta parallel so'rovdan aynan bittasi 1 qaytaradi.          ║
    ╚══════════════════════════════════════════════════════════════════════╝

    `doctor_id` berilsa, u ham CAS shartiga kiradi (A2): boshqa shifokorning
    sloti poyga holatida ham band qilinmaydi. Muddati o'tgan hold ham bo'sh
    hisoblanadi (E8, lazy expiry).
    """
    now = timezone.now()
    expires_at = now + timedelta(minutes=ttl_minutes)

    filters = {"id": slot_id}
    if doctor_id is not None:
        filters["doctor_id"] = doctor_id

    # Hold va yo'l buferi BITTA tranzaksiyada: bufer yopishda xato bo'lsa,
    # slot HELD bo'lib, qo'shnilari ochiq qolmasligi kerak. Chaqiruvchi
    # (create_booking) allaqachon tranzaksiyada bo'lsa, bu savepoint bo'ladi.
    with transaction.atomic():
        updated = TimeSlot.objects.filter(
            _free_or_expired_hold(now), **filters
        ).update(
            status=TimeSlot.Status.HELD,
            hold_expires_at=expires_at,
            updated_at=timezone.now(),
        )

        if updated == 0:
            raise SlotNotAvailable(f"Slot {slot_id} bo'sh emas")

        slot = TimeSlot.objects.get(id=slot_id)

        # Uy chaqiruvi bo'lsa, yo'l vaqti uchun qo'shni slotlarni ham yopamiz
        if slot.clinic_id is None:
            slot.travel_buffer_minutes = buffer_minutes or HOME_VISIT_BUFFER_MINUTES
            TimeSlot.objects.filter(id=slot.id).update(travel_buffer_minutes=slot.travel_buffer_minutes)
            _block_travel_buffer(slot)

    return slot


def _block_travel_buffer(slot: TimeSlot) -> int:
    """Uy chaqiruvidan oldin va keyin yo'l vaqtini yopadi.

    Shifokor soat 12:00 da bemor uyida bo'lsa, 12:30 da boshqa manzilda
    bo'la olmaydi — yo'lda vaqt ketadi.

    Bu yerda compare-and-set yetmaydi, chunki bir nechta qator bilan ishlaymiz.
    `select_for_update` bilan qulflaymiz. Qatorlarni HAR DOIM bir xil tartibda
    (start_at bo'yicha) qulflang — aks holda ikki tranzaksiya bir-birini kutib
    deadlock bo'ladi.

    Faqat `hold_slot` ning tranzaksiyasi ichidan chaqiriladi — o'zi yangi
    tranzaksiya ochmaydi (`select_for_update` tranzaksiyasiz ishlamaydi).
    """
    buffer = timedelta(minutes=slot.travel_buffer_minutes or HOME_VISIT_BUFFER_MINUTES)

    neighbours = (
        TimeSlot.objects.select_for_update()
        .filter(
            doctor_id=slot.doctor_id,
            status=TimeSlot.Status.FREE,
            start_at__gte=slot.start_at - buffer,
            start_at__lte=slot.end_at + buffer,
        )
        .exclude(id=slot.id)
        .order_by("start_at")
    )

    blocked_ids = [n.id for n in neighbours]
    if not blocked_ids:
        return 0

    return TimeSlot.objects.filter(id__in=blocked_ids).update(
        status=TimeSlot.Status.BLOCKED,
        block_reason=TimeSlot.BlockReason.TRAVEL_BUFFER,
        updated_at=timezone.now(),
    )


def confirm_slot(slot_id: UUID) -> None:
    """To'lov muvaffaqiyatli — HELD -> BOOKED."""
    updated = TimeSlot.objects.filter(
        id=slot_id, status=TimeSlot.Status.HELD
    ).update(
        status=TimeSlot.Status.BOOKED,
        hold_expires_at=None,
        updated_at=timezone.now(),
    )
    if updated == 0:
        raise SlotNotAvailable(f"Slot {slot_id} HELD holatida emas")


def release_slot(slot_id: UUID) -> None:
    """Kompensatsiya amali: HELD yoki BOOKED -> FREE.

    Faza 4 da saga aynan shu funksiyani chaqiradi, agar to'lov muvaffaqiyatsiz
    bo'lsa. Shu sababli u IDEMPOTENT bo'lishi shart — ikki marta chaqirilsa
    ham xato bermasligi kerak.
    """
    slot = TimeSlot.objects.filter(id=slot_id).first()
    if slot is None or slot.status == TimeSlot.Status.FREE:
        return  # allaqachon bo'sh — bu xato emas

    with transaction.atomic():
        TimeSlot.objects.filter(id=slot_id).update(
            status=TimeSlot.Status.FREE,
            hold_expires_at=None,
            travel_buffer_minutes=None,
            updated_at=timezone.now(),
        )
        # Yo'l vaqti uchun yopilgan qo'shnilarni ham qaytaramiz
        if slot.clinic_id is None:
            buffer = timedelta(minutes=slot.travel_buffer_minutes or HOME_VISIT_BUFFER_MINUTES)
            # ⬇ Faqat YO'L BUFERI bloklari ochiladi — ta'til yoki shifokor
            # qo'lda yopgan slotlarga tegilmaydi.
            TimeSlot.objects.filter(
                doctor_id=slot.doctor_id,
                status=TimeSlot.Status.BLOCKED,
                block_reason=TimeSlot.BlockReason.TRAVEL_BUFFER,
                start_at__gte=slot.start_at - buffer,
                start_at__lte=slot.end_at + buffer,
            ).exclude(id=slot.id).update(
                status=TimeSlot.Status.FREE,
                block_reason=TimeSlot.BlockReason.NONE,
                updated_at=timezone.now(),
            )


EXPIRE_BATCH_SIZE = 1000


def expired_hold_backlog() -> int:
    """Hali tozalanmagan muddati o'tgan hold'lar soni (E9 metrikasi)."""
    return TimeSlot.objects.filter(
        status=TimeSlot.Status.HELD, hold_expires_at__lt=timezone.now()
    ).count()


def expired_hold_slot_ids(limit: int = EXPIRE_BATCH_SIZE) -> list[UUID]:
    """Muddati o'tgan HELD slotlar ID'lari.

    `booking.expire_pending_bookings` shu funksiyadan foydalanadi — `TimeSlot`
    modeliga to'g'ridan-to'g'ri murojaat qilmasligi uchun (modul chegarasi).
    """
    return list(
        TimeSlot.objects.filter(
            status=TimeSlot.Status.HELD,
            hold_expires_at__lt=timezone.now(),
        ).values_list("id", flat=True)[:limit]
    )


def release_expired_holds() -> int:
    """Muddati o'tgan hold'larni bo'shatadi.

    Bu funksiya har daqiqada background job sifatida ishlaydi (Faza 3 da Celery,
    keyinroq Kubernetes CronJob).

    Nega kerak: mijoz to'lov oynasini ochib, uni yopib qo'ysa, slot abadiy
    HELD holatida qolib ketadi va hech kim uni band qila olmaydi.
    """
    expired = expired_hold_slot_ids()

    for slot_id in expired:
        release_slot(slot_id)

    if expired:
        logger.info("Muddati o'tgan hold bo'shatildi: %s ta", len(expired))

    return len(expired)