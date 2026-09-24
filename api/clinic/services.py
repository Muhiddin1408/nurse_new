"""
clinic/services.py — klinika admini (D7) va xonalar (D8).

Klinika admini faqat O'Z klinikasi obyektlarini ko'radi va o'zgartiradi:
har bir funksiya `clinic_id` ni oladi, view uni `request.clinic_ids` dan
tekshirib beradi (begona klinika — 404, mavjudligi ham oshkor qilinmaydi).

Shifokor bilan bog'lanish IKKI TOMONLAMA (D7): klinika taklif qiladi
(`invite`), shifokor qabul qiladi (`accept_invite`). Klinika shifokorni
o'ziga yozib ololmaydi.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from api import audit
from api.events import services as events
from api.schedule import cache as schedule_cache
from api.schedule import services as schedule_services
from api.schedule.services import LOCAL_TZ
from apps.booking.models import Booking
from apps.catalog.models import Clinic, Doctor, DoctorAffiliation, Room
from apps.schedule.models import ClinicClosure, TimeSlot, WorkingRule

logger = logging.getLogger(__name__)

A = DoctorAffiliation.Status
AFFILIATION_BLOCK_NOTE = "Klinika bilan aloqa to'xtatilgan"
ROOM_BUSY_NOTE = "Xona band"


class ClinicError(Exception):
    """Biznes qoidasi (409)."""


class NotFound(ClinicError):
    pass


# ---------------------------------------------------------------------------
# Yordamchilar
# ---------------------------------------------------------------------------


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
    return start, start + timedelta(days=1)


def _invalidate(doctor_id: UUID, start: datetime, end: datetime) -> None:
    day, last = start.astimezone(LOCAL_TZ).date(), end.astimezone(LOCAL_TZ).date()
    while day <= last:
        transaction.on_commit(lambda d=day: schedule_cache.invalidate_doctor_day(doctor_id, d))
        day += timedelta(days=1)


def _live_bookings():
    now = timezone.now()
    return Booking.objects.filter(
        Q(status=Booking.Status.CONFIRMED)
        | Q(status=Booking.Status.PENDING_PAYMENT, slot__hold_expires_at__gte=now)
    )


def _short(full_name: str) -> str:
    parts = full_name.split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else full_name


# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------

# Nom, manzil, koordinata — moderatsiyasiz o'zgarmaydi (bemor boshqa joyga borib qoladi)
PROFILE_FIELDS = ("description", "phone", "working_hours", "photo_url")


def _validate_working_hours(value: dict) -> None:
    if not isinstance(value, dict):
        raise ClinicError("working_hours: obyekt bo'lishi kerak")
    for key, span in value.items():
        if key not in {str(i) for i in range(7)}:
            raise ClinicError("working_hours kaliti: '0' (dushanba) … '6' (yakshanba)")
        if span is None:
            continue
        try:
            opens, closes = (time.fromisoformat(x) for x in span)
        except (TypeError, ValueError):
            raise ClinicError("working_hours: [\"08:00\", \"18:00\"] yoki null") from None
        if opens >= closes:
            raise ClinicError("working_hours: ochilish yopilishdan oldin bo'lishi kerak")


def update_profile(clinic_id: UUID, data: dict, *, user) -> Clinic:
    unknown = set(data) - set(PROFILE_FIELDS)
    if unknown:
        raise ClinicError(f"Bu maydonlar moderatsiyasiz o'zgarmaydi: {', '.join(sorted(unknown))}")
    if "working_hours" in data:
        _validate_working_hours(data["working_hours"])
    with transaction.atomic():
        clinic = Clinic.objects.select_for_update().get(id=clinic_id)
        before = {f: getattr(clinic, f) for f in data}
        for field, value in data.items():
            setattr(clinic, field, value)
        clinic.save()
        audit.record("clinic.update", obj=clinic, actor=user, before=before, after=data)
    return clinic


# ---------------------------------------------------------------------------
# Shifokorlar va takliflar
# ---------------------------------------------------------------------------


def list_doctors(clinic_id: UUID):
    return (
        DoctorAffiliation.objects.filter(clinic_id=clinic_id)
        .exclude(status=A.DECLINED)
        .select_related("doctor__user")
        .prefetch_related("doctor__specializations")
        .order_by("status", "doctor__user__full_name")
    )


def invite(clinic_id: UUID, *, phone: str, position: str, user) -> DoctorAffiliation:
    doctor = Doctor.objects.filter(user__phone=phone.strip()).select_related("user").first()
    if doctor is None:
        raise NotFound("Bu raqamda shifokor profili yo'q — shifokor avval ilovada ro'yxatdan o'tsin")
    with transaction.atomic():
        aff = DoctorAffiliation.objects.select_for_update().filter(doctor=doctor, clinic_id=clinic_id).first()
        if aff and aff.status in (A.ACTIVE, A.PAUSED):
            raise ClinicError("Shifokor allaqachon klinikada")
        if aff and aff.status == A.INVITED:
            raise ClinicError("Taklif allaqachon yuborilgan")
        fields = dict(status=A.INVITED, is_active=False, position=position, invited_by=user, responded_at=None)
        if aff is None:
            aff = DoctorAffiliation.objects.create(doctor=doctor, clinic_id=clinic_id, **fields)
        else:
            for k, v in fields.items():
                setattr(aff, k, v)
            aff.save()
        audit.record("clinic.invite", obj=aff, actor=user, after={"doctor_id": doctor.id, "position": position})
        clinic_name = Clinic.objects.filter(id=clinic_id).values_list("name", flat=True).first()
        events.publish(
            topic="doctor.notifications", key=str(doctor.id), event_type="ClinicInviteSent",
            payload={"doctor_id": str(doctor.id), "affiliation_id": str(aff.id), "clinic": clinic_name},
        )
    return aff


def _set_status(aff: DoctorAffiliation, status: str, *, actor, action: str) -> DoctorAffiliation:
    before = aff.status
    aff.status = status
    aff.is_active = status == A.ACTIVE
    aff.responded_at = timezone.now() if before == A.INVITED else aff.responded_at
    aff.save(update_fields=["status", "is_active", "responded_at", "updated_at"])
    audit.record(action, obj=aff, actor=actor, before={"status": before}, after={"status": status})
    # Faol emas -> klinikadagi bo'sh slotlari sotilmasin; faol -> qaytadi
    now = timezone.now()
    free = TimeSlot.objects.filter(doctor_id=aff.doctor_id, clinic_id=aff.clinic_id, start_at__gte=now)
    if status == A.ACTIVE:
        free.filter(status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.MANUAL,
                    block_note=AFFILIATION_BLOCK_NOTE).update(
            status=TimeSlot.Status.FREE, block_reason="", block_note="", updated_at=now)
    else:
        free.filter(schedule_services._free_or_expired_hold(now)).update(
            status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.MANUAL,
            block_note=AFFILIATION_BLOCK_NOTE, hold_expires_at=None, updated_at=now)
    _invalidate(aff.doctor_id, now, now + timedelta(days=schedule_services.GENERATION_HORIZON_DAYS + 1))
    return aff


def change_affiliation(clinic_id: UUID, affiliation_id: UUID, action: str, *, user) -> tuple[DoctorAffiliation, int]:
    """Klinika tomoni: pause | resume | end. Qaytaradi: (affiliatsiya, saqlangan faol bronlar soni).

    Faol bronlar AVTOMAT bekor qilinmaydi — reception ularni bemor bilan hal qiladi."""
    transitions = {
        "pause": ({A.ACTIVE}, A.PAUSED),
        "resume": ({A.PAUSED}, A.ACTIVE),
        "end": ({A.ACTIVE, A.PAUSED, A.INVITED}, A.ENDED),
    }
    if action not in transitions:
        raise ClinicError("action: pause | resume | end")
    allowed, target = transitions[action]
    with transaction.atomic():
        aff = DoctorAffiliation.objects.select_for_update().filter(id=affiliation_id, clinic_id=clinic_id).first()
        if aff is None:
            raise NotFound("not_found")
        if aff.status not in allowed:
            raise ClinicError(f"Holat {aff.status} dan {target} ga o'tib bo'lmaydi")
        _set_status(aff, target, actor=user, action=f"clinic.affiliation.{action}")
        kept = _live_bookings().filter(doctor_id=aff.doctor_id, slot__clinic_id=clinic_id,
                                       slot__start_at__gte=timezone.now()).count()
    return aff, kept


# Shifokor tomoni


def doctor_invites(doctor: Doctor):
    return DoctorAffiliation.objects.filter(doctor=doctor, status=A.INVITED).select_related("clinic")


def respond_invite(doctor: Doctor, affiliation_id: UUID, *, accept: bool) -> DoctorAffiliation:
    with transaction.atomic():
        aff = DoctorAffiliation.objects.select_for_update().filter(
            id=affiliation_id, doctor=doctor, status=A.INVITED).first()
        if aff is None:
            raise NotFound("not_found")
        return _set_status(aff, A.ACTIVE if accept else A.DECLINED, actor=doctor.user,
                           action="doctor.invite.accept" if accept else "doctor.invite.decline")


def leave_clinic(doctor: Doctor, affiliation_id: UUID) -> DoctorAffiliation:
    with transaction.atomic():
        aff = DoctorAffiliation.objects.select_for_update().filter(
            id=affiliation_id, doctor=doctor, status__in=[A.ACTIVE, A.PAUSED]).first()
        if aff is None:
            raise NotFound("not_found")
        return _set_status(aff, A.ENDED, actor=doctor.user, action="doctor.affiliation.leave")


# ---------------------------------------------------------------------------
# Jadval va dam olish kunlari
# ---------------------------------------------------------------------------


def schedule_overview(clinic_id: UUID, day: date) -> list[dict]:
    """Klinikadagi BARCHA shifokorlarning shu kungi slotlari (umumiy ko'rinish)."""
    start, end = _day_bounds(day)
    slots = (
        TimeSlot.objects.filter(clinic_id=clinic_id, start_at__gte=start, start_at__lt=end)
        .select_related("doctor__user", "room")
        .order_by("doctor__user__full_name", "start_at")
    )
    by_doctor: dict = {}
    for s in slots:
        row = by_doctor.setdefault(s.doctor_id, {"doctor_id": str(s.doctor_id),
                                                 "doctor": s.doctor.user.full_name, "slots": []})
        row["slots"].append({
            "slot_id": str(s.id), "start_at": s.start_at.isoformat(), "end_at": s.end_at.isoformat(),
            "status": s.status, "block_reason": s.block_reason,
            "room": s.room.name if s.room_id else None,
        })
    return list(by_doctor.values())


def create_closure(clinic_id: UUID, *, day: date, reason: str, user) -> tuple[ClinicClosure, list[dict]]:
    """Klinika yopiq kun. Bo'sh slotlar yopiladi; FAOL bronlar bekor qilinmaydi —
    ro'yxati qaytariladi, reception bemorlarga qo'ng'iroq qilib ko'chiradi."""
    if day <= timezone.localtime(timezone.now(), LOCAL_TZ).date():
        raise ClinicError("Dam olish kunini faqat kelajak uchun qo'yish mumkin")
    start, end = _day_bounds(day)
    try:
        with transaction.atomic():
            closure = ClinicClosure.objects.create(clinic_id=clinic_id, date=day, reason=reason)
            doctor_ids = set(TimeSlot.objects.filter(clinic_id=clinic_id, start_at__gte=start, start_at__lt=end)
                             .values_list("doctor_id", flat=True))
            TimeSlot.objects.filter(
                schedule_services._free_or_expired_hold(timezone.now()),
                clinic_id=clinic_id, start_at__gte=start, start_at__lt=end,
            ).update(status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.CLINIC_CLOSED,
                     block_note=reason[:255], hold_expires_at=None, updated_at=timezone.now())
            for doctor_id in doctor_ids:
                _invalidate(doctor_id, start, start)
            audit.record("clinic.closure.create", obj=closure, actor=user, after={"date": day, "reason": reason})
    except IntegrityError:
        raise ClinicError("Bu kun allaqachon dam olish kuni") from None
    conflicts = [
        {"booking_number": b.number, "doctor": b.doctor.user.full_name, "start_at": b.slot.start_at.isoformat(),
         "patient": _short(b.patient.full_name)}
        for b in _live_bookings().filter(slot__clinic_id=clinic_id, slot__start_at__gte=start, slot__start_at__lt=end)
        .select_related("slot", "doctor__user", "patient")
    ]
    return closure, conflicts


def delete_closure(clinic_id: UUID, closure_id: UUID, *, user) -> None:
    with transaction.atomic():
        closure = ClinicClosure.objects.filter(id=closure_id, clinic_id=clinic_id).first()
        if closure is None:
            raise NotFound("not_found")
        start, end = _day_bounds(closure.date)
        slots = TimeSlot.objects.filter(clinic_id=clinic_id, start_at__gte=start, start_at__lt=end,
                                        status=TimeSlot.Status.BLOCKED,
                                        block_reason=TimeSlot.BlockReason.CLINIC_CLOSED)
        doctor_ids = set(slots.values_list("doctor_id", flat=True))
        slots.update(status=TimeSlot.Status.FREE, block_reason="", block_note="", updated_at=timezone.now())
        audit.record("clinic.closure.delete", obj=closure, actor=user, before={"date": closure.date})
        closure.delete()
        for doctor_id in doctor_ids:
            _invalidate(doctor_id, start, start)
    # Yopiq kunda umuman generatsiya qilinmagan slotlar endi yaratilsin
    for doctor_id in WorkingRule.objects.filter(clinic_id=clinic_id).values_list("doctor_id", flat=True).distinct():
        schedule_services.generate_slots(doctor_id)


# ---------------------------------------------------------------------------
# Qabullar (reception) va hisobot
# ---------------------------------------------------------------------------


def appointments(clinic_id: UUID, day: date, q: str = "") -> list[dict]:
    start, end = _day_bounds(day)
    qs = (
        Booking.objects.filter(slot__clinic_id=clinic_id, slot__start_at__gte=start, slot__start_at__lt=end)
        .exclude(status__in=[Booking.Status.EXPIRED])
        .select_related("slot__room", "doctor__user", "patient", "client")
        .order_by("slot__start_at")
    )
    if q.strip():
        term = q.strip()
        qs = qs.filter(Q(client__phone__icontains=term) | Q(patient__full_name__icontains=term)
                       | Q(number__icontains=term))
    return [
        {
            "booking_id": str(b.id), "number": b.number, "status": b.status,
            "start_at": b.slot.start_at.isoformat(), "end_at": b.slot.end_at.isoformat(),
            "doctor": b.doctor.user.full_name, "room": b.slot.room.name if b.slot.room_id else None,
            # Reception bemor bilan gaplashadi — to'liq ism va telefon kerak (faqat O'Z klinikasi)
            "patient": b.patient.full_name, "client_phone": b.client.phone,
            "payment_mode": b.payment_mode, "total_price": str(b.total_price),
            "to_collect": str(b.total_price - b.prepay_amount) if b.status == Booking.Status.CONFIRMED else "0",
        }
        for b in qs
    ]


def report(clinic_id: UUID, date_from: date, date_to: date) -> dict:
    if date_to < date_from or (date_to - date_from).days > 366:
        raise ClinicError("Oraliq: date_from <= date_to, ko'pi bilan 1 yil")
    start, _ = _day_bounds(date_from)
    _, end = _day_bounds(date_to)
    slots = TimeSlot.objects.filter(clinic_id=clinic_id, start_at__gte=start, start_at__lt=end)
    bookings = Booking.objects.filter(slot__clinic_id=clinic_id, slot__start_at__gte=start, slot__start_at__lt=end)
    done = [Booking.Status.COMPLETED, Booking.Status.NO_SHOW]

    def stats(slot_qs, booking_qs) -> dict:
        s = slot_qs.aggregate(total=Count("id", filter=~Q(status=TimeSlot.Status.BLOCKED)),
                              booked=Count("id", filter=Q(status=TimeSlot.Status.BOOKED)))
        b = booking_qs.aggregate(
            revenue=Sum("total_price", filter=Q(status=Booking.Status.COMPLETED)),
            finished=Count("id", filter=Q(status__in=done)),
            no_show=Count("id", filter=Q(status=Booking.Status.NO_SHOW, no_show_by="client")),
            cancelled=Count("id", filter=Q(status=Booking.Status.CANCELLED)),
        )
        return {
            "revenue": str(b["revenue"] or Decimal("0")),
            "slots_total": s["total"], "slots_booked": s["booked"],
            "occupancy_pct": round(s["booked"] * 100 / s["total"], 1) if s["total"] else 0.0,
            "completed_or_no_show": b["finished"], "no_show": b["no_show"],
            "no_show_pct": round(b["no_show"] * 100 / b["finished"], 1) if b["finished"] else 0.0,
            "cancelled": b["cancelled"],
        }

    per_doctor = []
    for doctor_id, name in (Doctor.objects.filter(id__in=slots.values("doctor_id"))
                            .values_list("id", "user__full_name").order_by("user__full_name")):
        per_doctor.append({"doctor_id": str(doctor_id), "doctor": name,
                           **stats(slots.filter(doctor_id=doctor_id), bookings.filter(doctor_id=doctor_id))})
    return {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "total": stats(slots, bookings), "doctors": per_doctor}


# ---------------------------------------------------------------------------
# D8 — xonalar
# ---------------------------------------------------------------------------


def create_room(clinic_id: UUID, data: dict, *, user) -> Room:
    try:
        with transaction.atomic():  # savepoint: xato tashqi tranzaksiyani buzmasin
            room = Room.objects.create(clinic_id=clinic_id, **data)
    except IntegrityError:
        raise ClinicError("Bu nomli xona allaqachon bor") from None
    audit.record("clinic.room.create", obj=room, actor=user, after=data)
    return room


def update_room(clinic_id: UUID, room_id: UUID, data: dict, *, user) -> Room:
    room = Room.objects.filter(id=room_id, clinic_id=clinic_id).first()
    if room is None:
        raise NotFound("not_found")
    for k, v in data.items():
        setattr(room, k, v)
    try:
        with transaction.atomic():
            room.save()
    except IntegrityError:
        raise ClinicError("Bu nomli xona allaqachon bor") from None
    audit.record("clinic.room.update", obj=room, actor=user, after=data)
    return room


def assign_room(clinic_id: UUID, rule_id: UUID, room_id: UUID | None, *, user) -> dict:
    """Shifokorning ish qoidasiga xona biriktirish. Kelajak slotlariga ham qo'llanadi:
        * xona bo'sh — slot shu xonaga o'tadi;
        * xona boshqa shifokorda band — BO'SH slot yopiladi ("Xona band"),
          BRONLI slot xonasiz qoladi va `conflicts` da qaytadi (reception hal qiladi).
    """
    from api.doctor.schedule import _covered

    rule = WorkingRule.objects.filter(id=rule_id, clinic_id=clinic_id).first()
    if rule is None:
        raise NotFound("not_found")
    room = None
    if room_id is not None:
        room = Room.objects.filter(id=room_id, clinic_id=clinic_id, is_active=True).first()
        if room is None:
            raise NotFound("not_found")

    now = timezone.now()
    moved = blocked = 0
    conflicts: list[str] = []
    with transaction.atomic():
        WorkingRule.objects.filter(id=rule.id).update(room=room, updated_at=now)
        rule.room = room
        slots = [s for s in TimeSlot.objects.select_for_update().filter(
            doctor_id=rule.doctor_id, clinic_id=clinic_id, start_at__gte=now).order_by("start_at")
            if _covered(s, [rule])]
        for s in slots:
            if room is None:
                TimeSlot.objects.filter(id=s.id).update(room=None, updated_at=now)
                continue
            busy = TimeSlot.objects.filter(room=room, start_at__lt=s.end_at, end_at__gt=s.start_at).exclude(id=s.id)
            if not busy.exists():
                TimeSlot.objects.filter(id=s.id).update(room=room, updated_at=now)
                moved += 1
            elif s.status == TimeSlot.Status.FREE:
                TimeSlot.objects.filter(id=s.id).update(
                    room=None, status=TimeSlot.Status.BLOCKED, block_reason=TimeSlot.BlockReason.MANUAL,
                    block_note=ROOM_BUSY_NOTE, updated_at=now)
                blocked += 1
            else:
                conflicts.append(s.start_at.isoformat())
        audit.record("clinic.rule.assign_room", obj=rule, actor=user, after={"room_id": room_id})
        if slots:
            _invalidate(rule.doctor_id, slots[0].start_at, slots[-1].start_at)
    return {"moved": moved, "blocked": blocked, "conflicts": conflicts}


def clinic_rules(clinic_id: UUID):
    return (WorkingRule.objects.filter(clinic_id=clinic_id)
            .select_related("doctor__user", "room").order_by("doctor__user__full_name", "weekday", "start_time"))



