"""
doctor/earnings.py — daromad paneli va payout (D6, B8 ning payout qismi).

"Qancha ishladim?" savoliga javob. Pul qoidasi bitta joyda:

    shifokor ulushi (bron uchun) = doctor_payout − refundlarning shifokor ulushi
    refundning shifokor ulushi   = refund − refund × commission_rate

Hisobga olinadi: `completed` va `no_show(mijoz kelmadi)` — ikkalasida shifokor
vaqt sarflagan. `no_show(shifokor kelmadi)` va bekor qilinganlar — yo'q.
Faqat haqiqatan to'langan bronlar (to'lov `succeeded`/qisman/to'liq qaytarilgan).

Payout: haftalik (Du–Ya, Toshkent), minimal summa `PAYOUT_MIN_AMOUNT` dan kam
bo'lsa keyingi haftaga o'tadi. Bron `PayoutLine.booking` UNIQUE — ikki marta
to'lanmaydi.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from api import audit
from django.db.models import Q, Sum
from django.utils import timezone

from api.booking.pricing import split_amount
from api.events import services as events
from api.payments import ledger
from api.schedule.services import LOCAL_TZ
from apps.booking.models import Booking
from apps.catalog.models import Doctor
from apps.payment.models import Payment, PayoutLine, PayoutPeriod, Refund

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
PAID_PAYMENT_STATUSES = (Payment.Status.SUCCEEDED, Payment.Status.PARTIALLY_REFUNDED, Payment.Status.REFUNDED)


class EarningsError(Exception):
    pass


@dataclass
class Line:
    booking_id: UUID
    number: str
    date: date
    patient: str
    services: list[str]
    status: str
    gross: Decimal
    platform_fee: Decimal
    provider_fee: Decimal
    refunds: Decimal
    net: Decimal
    payout_id: UUID | None = None
    payout_status: str | None = None


@dataclass
class Summary:
    period_from: date
    period_to: date
    bookings_count: int = 0
    gross: Decimal = ZERO
    platform_fee: Decimal = ZERO
    provider_fee: Decimal = ZERO
    refunds: Decimal = ZERO
    net: Decimal = ZERO
    paid_out: Decimal = ZERO
    awaiting_payout: Decimal = ZERO
    lines: list[Line] = field(default_factory=list)


def _eligible_qs(doctor_id: UUID):
    paid = Payment.objects.filter(status__in=PAID_PAYMENT_STATUSES).values("booking_id")
    return (Booking.objects.filter(doctor_id=doctor_id, id__in=paid)
            .filter(Q(status=Booking.Status.COMPLETED) | Q(status=Booking.Status.NO_SHOW, no_show_by="client"))
            .select_related("slot", "patient").prefetch_related("items"))


def compute_line(b: Booking) -> Line:
    if b.platform_fee + b.provider_fee + b.doctor_payout == b.total_price and b.doctor_payout > 0:
        platform_fee, provider_fee, payout = b.platform_fee, b.provider_fee, b.doctor_payout
    else:  # B8 dan oldingi bronlar — stavkasiz; joriy stavka bilan hisoblanadi
        from api.booking.pricing import current_rates

        s = split_amount(b.total_price, *current_rates())
        platform_fee, provider_fee, payout = s.platform_fee, s.provider_fee, s.doctor_payout

    refunds = Refund.objects.filter(booking=b).aggregate(s=Sum("amount"))["s"] or ZERO
    rate = b.commission_rate or Decimal(str(settings.PLATFORM_COMMISSION_RATE))
    doctor_refund_share = refunds - (refunds * rate).quantize(CENT, rounding=ROUND_HALF_UP)
    net = max(ZERO, payout - doctor_refund_share)
    parts = b.patient.full_name.split()
    return Line(
        booking_id=b.id, number=b.number, date=b.slot.start_at.astimezone(LOCAL_TZ).date(),
        patient=f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else b.patient.full_name,
        services=[i.service_name for i in b.items.all()], status=b.status,
        gross=b.total_price, platform_fee=platform_fee, provider_fee=provider_fee, refunds=refunds, net=net,
    )


def _range(period: str | None, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = timezone.localtime(timezone.now(), LOCAL_TZ).date()
    if period == "today":
        return today, today
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    if period == "month":
        return today.replace(day=1), today
    if date_from and date_to:
        if date_to < date_from or (date_to - date_from).days > 366:
            raise EarningsError("Oraliq noto'g'ri (max 1 yil)")
        return date_from, date_to
    raise EarningsError("period: today|week|month yoki from/to")


def summary(doctor: Doctor, *, period: str | None = None, date_from: date | None = None,
            date_to: date | None = None, with_lines: bool = False) -> Summary:
    frm, to = _range(period, date_from, date_to)
    start = datetime.combine(frm, time.min, tzinfo=LOCAL_TZ)
    end = datetime.combine(to + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    bookings = _eligible_qs(doctor.id).filter(slot__start_at__gte=start, slot__start_at__lt=end).order_by("slot__start_at")
    payout_lines = {pl.booking_id: pl for pl in PayoutLine.objects.filter(booking__in=bookings).select_related("payout")}

    s = Summary(period_from=frm, period_to=to)
    for b in bookings:
        line = compute_line(b)
        pl = payout_lines.get(b.id)
        if pl:
            line.payout_id, line.payout_status = pl.payout_id, pl.payout.status
        s.bookings_count += 1
        s.gross += line.gross
        s.platform_fee += line.platform_fee
        s.provider_fee += line.provider_fee
        s.refunds += line.refunds
        s.net += line.net
        if pl and pl.payout.status == PayoutPeriod.Status.PAID:
            s.paid_out += line.net
        else:
            s.awaiting_payout += line.net
        if with_lines:
            s.lines.append(line)
    return s


# ---------------------------------------------------------------------------
# Payout
# ---------------------------------------------------------------------------


def last_full_week(today: date | None = None) -> tuple[date, date]:
    today = today or timezone.localtime(timezone.now(), LOCAL_TZ).date()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7), this_monday - timedelta(days=1)


def build_payouts(period_start: date, period_end: date) -> list[PayoutPeriod]:
    """Davr uchun payout'lar. IDEMPOTENT: allaqachon kiritilgan bronlar qayta olinmaydi."""
    end = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    min_amount = Decimal(str(settings.PAYOUT_MIN_AMOUNT))
    created = []

    doctor_ids = (Booking.objects.filter(
        Q(status=Booking.Status.COMPLETED) | Q(status=Booking.Status.NO_SHOW, no_show_by="client"),
        slot__end_at__lt=end, payout_line__isnull=True,
    ).values_list("doctor_id", flat=True).distinct())

    for doctor_id in list(doctor_ids):
        bookings = list(_eligible_qs(doctor_id).filter(slot__end_at__lt=end, payout_line__isnull=True))
        lines = [compute_line(b) for b in bookings]
        net = sum((l.net for l in lines), ZERO)
        if not lines or net < min_amount:
            continue  # keyingi haftaga o'tadi
        try:
            with transaction.atomic():
                payout = PayoutPeriod.objects.create(
                    doctor_id=doctor_id, period_start=period_start, period_end=period_end,
                    bookings_count=len(lines), net_amount=net,
                    gross_amount=sum((l.gross for l in lines), ZERO),
                    platform_fee=sum((l.platform_fee for l in lines), ZERO),
                    provider_fee=sum((l.provider_fee for l in lines), ZERO),
                    refunds_amount=sum((l.refunds for l in lines), ZERO),
                )
                PayoutLine.objects.bulk_create(PayoutLine(
                    payout=payout, booking_id=l.booking_id, gross_amount=l.gross, platform_fee=l.platform_fee,
                    provider_fee=l.provider_fee, refunds_amount=l.refunds, net_amount=l.net,
                ) for l in lines)
        except IntegrityError:
            logger.warning("Payout allaqachon bor yoki bron boshqa payout'da: doctor=%s", doctor_id)
            continue
        created.append(payout)
    logger.info("Payout'lar yaratildi: %s ta (%s — %s)", len(created), period_start, period_end)
    return created


def mark_paid(payout_id: UUID, *, admin, bank_reference: str) -> PayoutPeriod:
    if not bank_reference.strip():
        raise EarningsError("Bank to'lov hujjati raqami majburiy")
    with transaction.atomic():
        payout = PayoutPeriod.objects.select_for_update().select_related("doctor__user").get(id=payout_id)
        if payout.status == PayoutPeriod.Status.PAID:
            return payout
        PayoutPeriod.objects.filter(id=payout.id).update(
            status=PayoutPeriod.Status.PAID, paid_at=timezone.now(), paid_by=admin,
            bank_reference=bank_reference, updated_at=timezone.now(),
        )
        payout.refresh_from_db()
        audit.record("payout.mark_paid", obj=payout, actor=admin,
                     after={"amount": payout.net_amount, "bank_reference": bank_reference})
        ledger.post_payout_paid(payout)
        events.publish(topic="doctor.notifications", key=str(payout.doctor_id), event_type="PayoutPaid", payload={
            "doctor_id": str(payout.doctor_id), "payout_id": str(payout.id), "amount": int(payout.net_amount),
            "period_start": payout.period_start.isoformat(), "period_end": payout.period_end.isoformat(),
        })
    return payout


def statement_csv(payout: PayoutPeriod) -> str:
    """Excel ochadigan CSV (UTF-8 BOM, `;` ajratkich)."""
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["MedBron — to'lov hisoboti"])
    w.writerow(["Shifokor", payout.doctor.user.full_name])
    w.writerow(["Davr", payout.period_start.isoformat(), payout.period_end.isoformat()])
    w.writerow(["Holat", payout.get_status_display(), payout.bank_reference])
    w.writerow([])
    w.writerow(["Sana", "Bron", "Bemor", "Xizmatlar", "Holat", "Summa", "Komissiya", "Provayder", "Qaytarilgan", "Sof"])
    for pl in payout.lines.select_related("booking__slot", "booking__patient").prefetch_related("booking__items"):
        line = compute_line(pl.booking)
        w.writerow([line.date.isoformat(), line.number, line.patient, ", ".join(line.services), line.status,
                    pl.gross_amount, pl.platform_fee, pl.provider_fee, pl.refunds_amount, pl.net_amount])
    w.writerow([])
    w.writerow(["JAMI", "", "", "", payout.bookings_count, payout.gross_amount, payout.platform_fee,
                payout.provider_fee, payout.refunds_amount, payout.net_amount])
    return buf.getvalue()
