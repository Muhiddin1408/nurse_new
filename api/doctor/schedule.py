"""
doctor/schedule.py — shifokor o'z jadvalini boshqaradi (D3).

ENG NOZIK JOY — mavjud bronlar bilan to'qnashuv. Shifokor 15-sentabrga ta'til
qo'ysa, lekin o'sha kunda tasdiqlangan qabullar bo'lsa, bu HECH QACHON jim hal
qilinmaydi. Amal 409 + to'qnashuvlar ro'yxati bilan qaytadi va shifokor
`resolution` bilan qayta yuboradi:

    cancel_and_refund  — to'qnashgan bronlar bekor, mijozga 100% refund + SMS
    keep_bookings      — bronlar saqlanadi (shifokor ularni o'zi qabul qiladi),
                         faqat YANGI bronlar yopiladi

Bu modul — orkestrator: jadval (`apps.schedule`) va bron (`api.booking.services`)
ni birlashtiradi. Shuning uchun u `api/doctor/` da, `api/schedule/` da emas —
jadval moduli bron haqida bilmasligi kerak.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from api.booking import services as booking_services
from api.schedule import cache as schedule_cache
from api.schedule import services as schedule_services
from apps.booking.models import Booking
from apps.catalog.models import Doctor, DoctorAffiliation
from apps.schedule.models import TimeOff, TimeSlot, WorkingRule

logger = logging.getLogger(__name__)

LOCAL_TZ = schedule_services.LOCAL_TZ
ALLOWED_SLOT_MINUTES = (10, 15, 20, 30, 40, 45, 60, 90, 120)
MAX_CALENDAR_DAYS = 31

RESOLUTION_CANCEL = "cancel_and_refund"
RESOLUTION_KEEP = "keep_bookings"
RESOLUTIONS = (RESOLUTION_CANCEL, RESOLUTION_KEEP)


class ScheduleError(Exception):
    """400 — so'rov noto'g'ri."""


class ScheduleConflict(Exception):
    """409 — amal mavjud faol bronlarga tegadi, shifokor qaror qilishi kerak."""

    def __init__(self, conflicts: list[dict]):
        super().__init__("Bu o'zgarish tasdiqlangan qabullarga ta'sir qiladi")
        self.conflicts = conflicts


@dataclass(frozen=True)
class ChangeResult:
    cancelled_bookings: list[str]
    kept_bookings: list[str]


# ---------------------------------------------------------------------------
# Yordamchilar
# ---------------------------------------------------------------------------


def _local(dt: datetime) -> datetime:
    return dt.astimezone(LOCAL_TZ)


def _active_future_bookings(doctor_id: UUID, *, start=None, end=None):
    now = timezone.now()
    live = Q(status=Booking.Status.CONFIRMED) | Q(
        status=Booking.Status.PENDING_PAYMENT, slot__hold_expires_at__gte=now
    )
    qs = Booking.objects.filter(live, doctor_id=doctor_id, slot__start_at__gte=now)
    if start is not None:
        qs = qs.filter(slot__end_at__gt=start)
    if end is not None:
        qs = qs.filter(slot__start_at__lt=end)
    return qs.select_related("slot", "patient").order_by("slot__start_at")


def _conflict_row(b: Booking) -> dict:
    return {
        "booking_id": str(b.id),
        "booking_number": b.number,
        "status": b.status,
        "start_at": b.slot.start_at.isoformat(),
        "end_at": b.slot.end_at.isoformat(),
        # Maxfiylik: to'liq ism emas, "A. Karimov" ko'rinishi yetarli
        "patient": _short_name(b.patient.full_name),
    }


def _short_name(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) < 2:
        return full_name
    return f"{parts[0][0]}. {' '.join(parts[1:])}"


def _resolve(conflicts: list[Booking], resolution: str | None, reason: str) -> ChangeResult:
    if conflicts and resolution not in RESOLUTIONS:
        raise ScheduleConflict([_conflict_row(b) for b in conflicts])
    cancelled, kept = [], []
    for b in conflicts:
        if resolution == RESOLUTION_CANCEL:
            booking_services.cancel_booking(b.id, reason=reason, actor="doctor")
            cancelled.append(b.number)
        else:
            kept.append(b.number)
    return ChangeResult(cancelled, kept)


def _invalidate_days(doctor_id: UUID, start: datetime, end: datetime) -> None:
    day = _local(start).date()
    last = _local(end).date()
    while day <= last:
        transaction.on_commit(lambda d=day: schedule_cache.invalidate_doctor_day(doctor_id, d))
        day += timedelta(days=1)


# ---------------------------------------------------------------------------
# Ish qoidalari (WorkingRule)
# ---------------------------------------------------------------------------


def _validate_rule(doctor: Doctor, data: dict, *, exclude_id: UUID | None = None) -> None:
    if not 0 <= data["weekday"] <= 6:
        raise ScheduleError("weekday 0 (dushanba) … 6 (yakshanba)")
    if data["start_time"] >= data["end_time"]:
        raise ScheduleError("Boshlanish vaqti tugashdan oldin bo'lishi kerak")
    if data["slot_minutes"] not in ALLOWED_SLOT_MINUTES:
        raise ScheduleError(f"slot_minutes: {ALLOWED_SLOT_MINUTES}")
    span = datetime.combine(date.min, data["end_time"]) - datetime.combine(date.min, data["start_time"])
    if span < timedelta(minutes=data["slot_minutes"]):
        raise ScheduleError("Oraliqqa kamida bitta slot sig'ishi kerak")
    if data.get("valid_to") and data["valid_to"] < data["valid_from"]:
        raise ScheduleError("valid_to valid_from dan oldin bo'lmasligi kerak")

    clinic_id = data.get("clinic_id")
    if clinic_id is None:
        if not doctor.accepts_home_visits:
            raise ScheduleError("Uy chaqiruvi qoidasi uchun profilda uy chaqiruvini yoqing")
    elif not DoctorAffiliation.objects.filter(
        doctor=doctor, clinic_id=clinic_id, is_active=True, clinic__status="active"
    ).exists():
        raise ScheduleError("Siz bu klinikada faol ishlamaysiz")

    # Bir kunda ustma-ust qoidalar — shifokor bir vaqtda ikki joyda
    overlapping = WorkingRule.objects.filter(
        doctor=doctor, weekday=data["weekday"],
        start_time__lt=data["end_time"], end_time__gt=data["start_time"],
    ).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=data["valid_from"]))
    if data.get("valid_to"):
        overlapping = overlapping.filter(valid_from__lte=data["valid_to"])
    if exclude_id:
        overlapping = overlapping.exclude(id=exclude_id)
    if overlapping.exists():
        raise ScheduleError("Bu kun va vaqtda boshqa ish qoidasi bor")


def _covered(slot: TimeSlot, rules: list[WorkingRule], *, exact: bool = False) -> bool:
    """Slot qoida ichidami.

    exact=False — bron uchun: ish vaqti ichida bo'lsa yetarli (shifokor qabul qila oladi).
    exact=True  — BO'SH slot uchun: qoidaning to'riga aniq mos (uzunlik va qadam).
                  Aks holda 30 -> 20 daqiqaga o'tganda eski bo'sh slotlar qolib,
                  yangi to'r ular bilan to'qnashardi.
    """
    start, end = _local(slot.start_at), _local(slot.end_at)
    d = start.date()
    for r in rules:
        if not (r.weekday == d.weekday()
                and r.valid_from <= d and (r.valid_to is None or d <= r.valid_to)
                and end.date() == d and r.start_time <= start.time() and end.time() <= r.end_time
                and r.clinic_id == slot.clinic_id):
            continue
        if not exact:
            return True
        offset = datetime.combine(d, start.time()) - datetime.combine(d, r.start_time)
        step = timedelta(minutes=r.slot_minutes)
        if end - start == step and offset % step == timedelta(0):
            return True
    return False


def _apply_rules_change(doctor: Doctor, resolution: str | None) -> ChangeResult:
    """Qoidalar o'zgargandan KEYIN (tranzaksiya ichida): qoplanmay qolgan bronlar va slotlar."""
    rules = list(WorkingRule.objects.filter(doctor=doctor))
    uncovered = [b for b in _active_future_bookings(doctor.id) if not _covered(b.slot, rules)]
    result = _resolve(uncovered, resolution, "Shifokor ish jadvalini o'zgartirdi")
    regenerate(doctor.id, rules=rules)
    return result


def list_rules(doctor: Doctor):
    return WorkingRule.objects.filter(doctor=doctor).order_by("weekday", "start_time")


def create_rule(doctor: Doctor, data: dict) -> WorkingRule:
    _validate_rule(doctor, data)
    with transaction.atomic():
        rule = WorkingRule.objects.create(doctor=doctor, **data)
        regenerate(doctor.id)
    return rule


def update_rule(doctor: Doctor, rule_id: UUID, data: dict, resolution: str | None) -> tuple[WorkingRule, ChangeResult]:
    rule = WorkingRule.objects.filter(id=rule_id, doctor=doctor).first()
    if rule is None:
        raise ScheduleError("Qoida topilmadi")
    merged = {
        "weekday": rule.weekday, "start_time": rule.start_time, "end_time": rule.end_time,
        "slot_minutes": rule.slot_minutes, "valid_from": rule.valid_from, "valid_to": rule.valid_to,
        "clinic_id": rule.clinic_id, **data,
    }
    _validate_rule(doctor, merged, exclude_id=rule.id)
    with transaction.atomic():
        WorkingRule.objects.filter(id=rule.id).update(**merged, updated_at=timezone.now())
        result = _apply_rules_change(doctor, resolution)
    rule.refresh_from_db()
    return rule, result


def delete_rule(doctor: Doctor, rule_id: UUID, resolution: str | None) -> ChangeResult:
    rule = WorkingRule.objects.filter(id=rule_id, doctor=doctor).first()
    if rule is None:
        raise ScheduleError("Qoida topilmadi")
    with transaction.atomic():
        rule.delete()
        return _apply_rules_change(doctor, resolution)


# ---------------------------------------------------------------------------
# Slotlarni qayta yaratish
# ---------------------------------------------------------------------------


def regenerate(doctor_id: UUID, *, rules: list[WorkingRule] | None = None, days: int | None = None) -> dict:
    """Qoidalarga mos kelmaydigan KELAJAK BO'SH slotlarni olib tashlaydi va yangilarini yaratadi.

    Band / ushlab turilgan / qo'lda yopilgan slotlarga tegilmaydi. Bron tarixi
    bor bo'sh slot o'chirilmaydi (bekor qilingan bron unga bog'langan) —
    `BLOCKED(manual)` qilinadi, shunda qayta sotilmaydi.
    """
    rules = rules if rules is not None else list(WorkingRule.objects.filter(doctor_id=doctor_id))
    now = timezone.now()
    stale = [
        s for s in TimeSlot.objects.filter(doctor_id=doctor_id, start_at__gte=now, status=TimeSlot.Status.FREE)
        if not _covered(s, rules, exact=True)
    ]
    stale_ids = [s.id for s in stale]
    with_history = set(
        Booking.objects.filter(slot_id__in=stale_ids).values_list("slot_id", flat=True)
    )
    removed = TimeSlot.objects.filter(id__in=[i for i in stale_ids if i not in with_history]).delete()[0]
    TimeSlot.objects.filter(id__in=with_history).update(
        status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.MANUAL,
        block_note="Ish qoidasi o'zgardi", updated_at=now,
    )
    created = schedule_services.generate_slots(doctor_id, days=days or schedule_services.GENERATION_HORIZON_DAYS)
    horizon_end = now + timedelta(days=(days or schedule_services.GENERATION_HORIZON_DAYS) + 1)
    _invalidate_days(doctor_id, now, horizon_end)
    return {"removed": removed + len(with_history), "created": created}


# ---------------------------------------------------------------------------
# Ta'til / dam olish
# ---------------------------------------------------------------------------


def create_time_off(doctor: Doctor, *, start_at: datetime, end_at: datetime, kind: str, reason: str,
                    resolution: str | None) -> tuple[TimeOff, ChangeResult]:
    if end_at <= start_at:
        raise ScheduleError("Tugash vaqti boshlanishdan keyin bo'lishi kerak")
    if end_at <= timezone.now():
        raise ScheduleError("O'tgan vaqtga ta'til qo'yib bo'lmaydi")
    if end_at - start_at > timedelta(days=90):
        raise ScheduleError("Bir martada 90 kundan uzun ta'til qo'yib bo'lmaydi")
    if TimeOff.objects.filter(doctor=doctor, start_at__lt=end_at, end_at__gt=start_at).exists():
        raise ScheduleError("Bu oraliqda boshqa ta'til bor")

    with transaction.atomic():
        conflicts = list(_active_future_bookings(doctor.id, start=start_at, end=end_at))
        result = _resolve(conflicts, resolution, f"Shifokor mavjud emas: {reason or kind}")
        time_off = TimeOff.objects.create(doctor=doctor, start_at=start_at, end_at=end_at, kind=kind, reason=reason)
        # Bo'sh (va muddati o'tgan hold) slotlar yopiladi; saqlangan bronlarning slotlariga tegilmaydi
        TimeSlot.objects.filter(
            schedule_services._free_or_expired_hold(timezone.now()),
            doctor=doctor, start_at__lt=end_at, end_at__gt=start_at,
        ).update(status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.TIME_OFF,
                 time_off=time_off, hold_expires_at=None, updated_at=timezone.now())
        _invalidate_days(doctor.id, start_at, end_at)
    logger.info("Ta'til: doctor=%s %s—%s, bekor=%s", doctor.id, start_at, end_at, result.cancelled_bookings)
    return time_off, result


def delete_time_off(doctor: Doctor, time_off_id: UUID) -> int:
    time_off = TimeOff.objects.filter(id=time_off_id, doctor=doctor).first()
    if time_off is None:
        raise ScheduleError("Ta'til topilmadi")
    with transaction.atomic():
        reopened = TimeSlot.objects.filter(
            time_off=time_off, status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.TIME_OFF,
            start_at__gte=timezone.now(),
        ).update(status=TimeSlot.Status.FREE, block_reason=TimeSlot.BlockReason.NONE, time_off=None,
                 updated_at=timezone.now())
        _invalidate_days(doctor.id, time_off.start_at, time_off.end_at)
        time_off.delete()
        # Ta'til paytida qoidadan chiqarilgan slotlar bo'lsa — qayta yaratiladi
        schedule_services.generate_slots(doctor.id)
    return reopened


# ---------------------------------------------------------------------------
# Bitta slotni yopish / ochish
# ---------------------------------------------------------------------------


def block_slot(doctor: Doctor, slot_id: UUID, note: str) -> TimeSlot:
    with transaction.atomic():
        updated = TimeSlot.objects.filter(
            schedule_services._free_or_expired_hold(timezone.now()), id=slot_id, doctor=doctor,
        ).update(status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.MANUAL,
                 block_note=note[:255], hold_expires_at=None, updated_at=timezone.now())
        slot = TimeSlot.objects.filter(id=slot_id, doctor=doctor).first()
        if slot is None:
            raise ScheduleError("Slot topilmadi")
        if not updated:
            raise ScheduleError("Faqat bo'sh slotni yopish mumkin — band slotni qabullar orqali bekor qiling")
        _invalidate_days(doctor.id, slot.start_at, slot.end_at)
    return slot


def unblock_slot(doctor: Doctor, slot_id: UUID) -> TimeSlot:
    with transaction.atomic():
        updated = TimeSlot.objects.filter(
            id=slot_id, doctor=doctor, status=TimeSlot.Status.BLOCKED,
            block_reason=TimeSlot.BlockReason.MANUAL, start_at__gt=timezone.now(),
        ).update(status=TimeSlot.Status.FREE, block_reason=TimeSlot.BlockReason.NONE, block_note="",
                 updated_at=timezone.now())
        slot = TimeSlot.objects.filter(id=slot_id, doctor=doctor).first()
        if slot is None:
            raise ScheduleError("Slot topilmadi")
        if not updated:
            raise ScheduleError("Faqat siz qo'lda yopgan kelajak slotni ochish mumkin")
        _invalidate_days(doctor.id, slot.start_at, slot.end_at)
    return slot


# ---------------------------------------------------------------------------
# Kalendar
# ---------------------------------------------------------------------------


def calendar(doctor: Doctor, date_from: date, date_to: date) -> list[dict]:
    if date_to < date_from:
        raise ScheduleError("date_to date_from dan oldin")
    if (date_to - date_from).days >= MAX_CALENDAR_DAYS:
        raise ScheduleError(f"Oraliq {MAX_CALENDAR_DAYS} kundan oshmasligi kerak")

    start = datetime.combine(date_from, time.min, tzinfo=LOCAL_TZ)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    slots = list(TimeSlot.objects.filter(doctor=doctor, start_at__gte=start, start_at__lt=end)
                 .select_related("clinic").order_by("start_at"))
    active = {
        b.slot_id: b for b in Booking.objects.filter(
            slot_id__in=[s.id for s in slots],
            status__in=[Booking.Status.PENDING_PAYMENT, Booking.Status.CONFIRMED,
                        Booking.Status.COMPLETED, Booking.Status.NO_SHOW],
        ).select_related("patient").prefetch_related("items")
    }
    now = timezone.now()
    rows = []
    for s in slots:
        status_ = s.status
        if status_ == TimeSlot.Status.HELD and s.hold_expires_at and s.hold_expires_at < now:
            status_ = TimeSlot.Status.FREE  # lazy expiry — shifokorga ham to'g'ri ko'rinsin
        b = active.get(s.id)
        rows.append({
            "slot_id": str(s.id),
            "start_at": s.start_at.isoformat(),
            "end_at": s.end_at.isoformat(),
            "status": status_,
            "block_reason": s.block_reason,
            "block_note": s.block_note,
            "place": "home" if s.clinic_id is None else "clinic",
            "clinic": s.clinic.name if s.clinic_id else None,
            "booking": None if b is None else {
                "id": str(b.id), "number": b.number, "status": b.status,
                "patient": _short_name(b.patient.full_name),
                "services": [i.service_name for i in b.items.all()],
            },
        })
    return rows
