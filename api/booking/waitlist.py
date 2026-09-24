"""
booking/waitlist.py — C10: navbat.

Slot band bo'lgani uchun bron qila olmagan mijoz "yo'qolgan talab". Uni
jadvalga yozib qo'yamiz va slot bo'shaganda (bekor qilish yoki to'lov
muddati o'tishi) navbatdagi BIRINCHI odamga xabar beramiz.

NEGA BITTA ODAM, HAMMASI EMAS: o'nta odamga bir vaqtda "bo'sh vaqt bor"
deb yozsak, to'qqiztasi kirib, slot band bo'lganini ko'radi. Bu eslatma
emas, umidsizlantirish — va ular keyingi xabarni ochmaydi ham.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.booking.models import WaitlistEntry
from api.schedule import services as schedule_services

logger = logging.getLogger(__name__)

Status = WaitlistEntry.Status

# Mijoz xabardan keyin shuncha vaqt ichida bron qilishi kutiladi. Bu
# muddat SLOTNI BAND QILMAYDI (hech kimga imtiyoz bermaymiz) — u faqat
# "bu odamga yaqinda aytdik" degan belgini eskirtiradi.
NOTIFY_WINDOW = timedelta(minutes=15)

# Bitta navbatga necha marta xabar berish mumkin. Chegarasiz qilsak,
# har bir bekor qilishda o'sha odamga SMS ketaveradi.
MAX_NOTIFICATIONS = 3

# Bo'shagan slot haqida xabar berishning ma'nosi bor eng qisqa muddat
MIN_LEAD_FOR_NOTICE = timedelta(hours=2)

ALTERNATIVES_LIMIT = 3


class WaitlistError(Exception):
    """Navbatga yozishdagi biznes xatosi."""


# ---------------------------------------------------------------------------
# Mijoz tomoni
# ---------------------------------------------------------------------------


def join(
    *, client_id: UUID, doctor_id: UUID, date_from: date, date_to: date, place: str
) -> WaitlistEntry:
    """Navbatga yozadi. Takroriy yozilish — xato emas, mavjudi yangilanadi."""
    if date_to < date_from:
        raise WaitlistError("date_to date_from dan oldin bo'lishi mumkin emas")
    if date_from < schedule_services.today_local():
        raise WaitlistError("O'tgan sanaga navbatga yozib bo'lmaydi")

    entry = WaitlistEntry.objects.filter(
        client_id=client_id, doctor_id=doctor_id, status__in=[Status.ACTIVE, Status.NOTIFIED]
    ).first()

    if entry is not None:
        # Oyna yoki joy o'zgargan bo'lishi mumkin — yangilaymiz va
        # navbatni qaytadan faollashtiramiz
        entry.date_from, entry.date_to, entry.place = date_from, date_to, place
        entry.status = Status.ACTIVE
        entry.save(update_fields=["date_from", "date_to", "place", "status", "updated_at"])
        return entry

    return WaitlistEntry.objects.create(
        client_id=client_id,
        doctor_id=doctor_id,
        date_from=date_from,
        date_to=date_to,
        place=place,
    )


def leave(*, client_id: UUID, entry_id: UUID) -> bool:
    """Navbatdan chiqadi. Begona yozuv — xuddi mavjud emasdek (A4)."""
    return bool(
        WaitlistEntry.objects.filter(
            id=entry_id, client_id=client_id, status__in=[Status.ACTIVE, Status.NOTIFIED]
        ).update(status=Status.CANCELLED, updated_at=timezone.now())
    )


def list_mine(client_id: UUID):
    return (
        WaitlistEntry.objects.filter(
            client_id=client_id, status__in=[Status.ACTIVE, Status.NOTIFIED]
        )
        .select_related("doctor__user")
        .order_by("-created_at")
    )


def mark_converted(*, client_id: UUID, doctor_id: UUID) -> int:
    """Mijoz shu shifokorga bron qildi — navbat o'z vazifasini bajardi.

    Yopilmasa, mijoz allaqachon bron qilgan bo'lsa ham keyingi bo'shagan
    slot haqida SMS olaverardi."""
    return WaitlistEntry.objects.filter(
        client_id=client_id, doctor_id=doctor_id, status__in=[Status.ACTIVE, Status.NOTIFIED]
    ).update(status=Status.CONVERTED, updated_at=timezone.now())


# ---------------------------------------------------------------------------
# Slot bo'shaganda
# ---------------------------------------------------------------------------


def next_candidate(*, doctor_id: UUID, start_at: datetime, place: str) -> WaitlistEntry | None:
    """Bo'shagan slotga mos navbatdagi BIRINCHI odam (eng erta yozilgani).

    `select_for_update` — ikkita bron bir vaqtda bekor qilinsa, ikkala
    oqim ham bitta odamni tanlab, unga ikki SMS yubormasligi uchun.
    """
    local_day = start_at.astimezone(schedule_services.LOCAL_TZ).date()
    cutoff = timezone.now() - NOTIFY_WINDOW

    return (
        WaitlistEntry.objects.select_for_update(skip_locked=True)
        .filter(
            doctor_id=doctor_id,
            place=place,
            date_from__lte=local_day,
            date_to__gte=local_day,
            status__in=[Status.ACTIVE, Status.NOTIFIED],
            notified_count__lt=MAX_NOTIFICATIONS,
        )
        # Yaqinda xabar berilganni qayta bezovta qilmaymiz. `isnull` sharti
        # SHART: usiz hech qachon xabar olmagan (notified_at=NULL) odamlar
        # — ya'ni navbatning ko'pchiligi — umuman tanlanmasdi.
        .filter(Q(notified_at__isnull=True) | Q(notified_at__lt=cutoff))
        .order_by("created_at")
        .first()
    )


def claim(entry: WaitlistEntry) -> None:
    """Xabar berildi deb belgilaydi."""
    WaitlistEntry.objects.filter(id=entry.id).update(
        status=Status.NOTIFIED,
        notified_at=timezone.now(),
        notified_count=entry.notified_count + 1,
        updated_at=timezone.now(),
    )


def notify_slot_freed(*, doctor_id: UUID, start_at: datetime, place: str, notify) -> bool:
    """Slot bo'shadi — navbatdagi birinchi odamga xabar beradi.

    `notify(entry, start_at)` — xabar yuborish funksiyasi (SMS moduli shu
    yerga import qilinmaydi: bron moduli bildirishnoma moduliga bog'lanmasin).

    Qaytaradi: xabar berildimi.
    """
    if start_at - timezone.now() < MIN_LEAD_FOR_NOTICE:
        # 2 soatdan kam qolgan vaqtga odam ulgurmaydi — SMS puli behuda
        return False

    with transaction.atomic():
        entry = next_candidate(doctor_id=doctor_id, start_at=start_at, place=place)
        if entry is None:
            return False
        claim(entry)

    try:
        notify(entry, start_at)
    except Exception:  # noqa: BLE001
        # Xabar ketmasa ham navbat buzilmasin — keyingi bo'shagan slotda
        # bu odam yana navbatda turadi (`notified_count` oshgan holda).
        logger.exception("Navbat xabari yuborilmadi: entry=%s", entry.id)
        return False

    logger.info("Navbat xabari: entry=%s, vaqt=%s", entry.id, start_at)
    return True


def find_alternatives(*, doctor_id: UUID, start_at: datetime, place: str) -> list[dict]:
    """Band slot o'rniga eng yaqin bo'sh vaqtlar (409 javobiga qo'shiladi).

    Mijozga "band" deyish — oqimning tugashi. Unga uchta muqobil ko'rsatish
    — o'sha so'rovning ichida yana bron qilish imkoni."""
    slots = schedule_services.get_available_slots(
        doctor_id=doctor_id, date_from=start_at - timedelta(days=1)
    )
    if place == "home":
        slots = slots.filter(clinic__isnull=True)
    else:
        slots = slots.filter(clinic__isnull=False)

    return [
        {"slot_id": str(s.id), "start_at": s.start_at.isoformat()}
        for s in slots[:ALTERNATIVES_LIMIT]
    ]
