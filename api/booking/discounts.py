"""
booking/discounts.py — chegirmalar (C12).

Uch manba, BIRLASHMAYDI — mijozga eng foydalisi qo'llanadi:

    promo          promo kod (`PromoCode`) — kim to'lashi kodda yozilgan
    first_booking  platformadagi birinchi bron — platforma to'laydi
                   (`settings.PRICING['first_booking_percent']`)
    follow_up      14 kun ichida o'sha shifokorga qayta qabul — shifokor
                   to'laydi (`Doctor.follow_up_discount_percent`)
    package        mijoz shifokorning paketiga (kursiga) yozilgan va seansi
                   qolgan — shifokor to'laydi (`PackageEnrollment`)

Chegirmalarni qo'shib yuborish (promo + birinchi bron + takroriy) — narxni
nolga tushirish va suiiste'mol yo'li; shuning uchun faqat bittasi.

Kim to'lashi taqsimotga ta'sir qiladi (B8, `pricing.split_with_discount`):
    platform — shifokor chegirmasiz narxdagi ulushini oladi, farq platforma
               komissiyasidan (kerak bo'lsa manfiy — marketing xarajati);
    doctor   — taqsimot chegirmali narxdan (odatiy split).

Dinamik narx bu yerda EMAS (`api/booking/time_pricing.py`): u chegirma emas,
balki `original_price` ning o'zini o'zgartiradi va shu sababli chegirmalar
bilan raqobatlashmaydi — avval narx tuzatiladi, keyin eng foydali chegirma.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.booking.models import Booking, PackageEnrollment, PromoCode, PromoRedemption

CENT = Decimal("0.01")
FOLLOW_UP_WINDOW = timedelta(days=14)


class PromoInvalid(Exception):
    """Promo kod ishlamaydi (400) — sababi matnda."""


@dataclass(frozen=True)
class Discount:
    amount: Decimal
    kind: str = ""        # "" | promo | first_booking | follow_up | package
    borne_by: str = ""    # "" | platform | doctor
    promo: PromoCode | None = None
    enrollment: PackageEnrollment | None = None

    @property
    def code(self) -> str:
        return self.promo.code if self.promo else ""


NONE = Discount(amount=Decimal("0"))


def _percent_of(total: Decimal, percent) -> Decimal:
    return (total * Decimal(percent) / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def _has_previous_booking(client_id: UUID) -> bool:
    return Booking.objects.filter(
        client_id=client_id,
        status__in=[Booking.Status.CONFIRMED, Booking.Status.COMPLETED, Booking.Status.NO_SHOW],
    ).exists()


def promo_discount(code: str, *, client_id: UUID, doctor_id: UUID, total: Decimal, now=None) -> Discount:
    now = now or timezone.now()
    promo = PromoCode.objects.filter(code=code.strip().upper(), is_active=True).first()
    if promo is None:
        raise PromoInvalid("Promo kod topilmadi")
    if (promo.valid_from and now < promo.valid_from) or (promo.valid_until and now > promo.valid_until):
        raise PromoInvalid("Promo kod muddati tugagan")
    if promo.doctor_id and promo.doctor_id != doctor_id:
        raise PromoInvalid("Promo kod bu shifokor uchun emas")
    if total < promo.min_amount:
        raise PromoInvalid(f"Promo kod {promo.min_amount:,.0f} so'mdan boshlab ishlaydi".replace(",", " "))
    active = PromoRedemption.objects.filter(promo=promo, is_active=True)
    if promo.max_uses is not None and active.count() >= promo.max_uses:
        raise PromoInvalid("Promo kod limiti tugagan")
    if active.filter(client_id=client_id).count() >= promo.max_uses_per_user:
        raise PromoInvalid("Bu promo koddan allaqachon foydalangansiz")
    if promo.first_booking_only and _has_previous_booking(client_id):
        raise PromoInvalid("Promo kod faqat birinchi bron uchun")

    amount = _percent_of(total, promo.value) if promo.kind == PromoCode.Kind.PERCENT else Decimal(promo.value)
    if promo.max_discount is not None:
        amount = min(amount, promo.max_discount)
    return Discount(amount=min(amount, total), kind="promo", borne_by=promo.borne_by, promo=promo)


def package_discount(*, client_id: UUID, doctor_id: UUID, service_prices: dict[UUID, Decimal],
                     now=None) -> Discount | None:
    """C12 paket: faol kursdan chegirma. Chegirma FAQAT kursga kiradigan
    xizmat narxiga qo'llanadi — bir bronda paket xizmati va boshqa xizmat
    birga bo'lsa, ikkinchisi to'liq narxda qoladi."""
    now = now or timezone.now()
    for enr in (
        PackageEnrollment.objects.filter(
            client_id=client_id, doctor_id=doctor_id, is_active=True,
            service_id__in=list(service_prices), expires_at__gt=now,
        ).order_by("expires_at")
    ):
        if enr.sessions_left <= 0:
            continue
        amount = _percent_of(service_prices[enr.service_id], enr.discount_percent)
        if amount > 0:
            return Discount(amount=amount, kind="package", borne_by="doctor", enrollment=enr)
    return None


def automatic_discounts(*, client_id: UUID, doctor_id: UUID, total: Decimal,
                        follow_up_percent: int, service_prices: dict[UUID, Decimal] | None = None,
                        now=None) -> list[Discount]:
    now = now or timezone.now()
    found = []
    first_percent = settings.PRICING["first_booking_percent"]
    if first_percent and not _has_previous_booking(client_id):
        found.append(Discount(_percent_of(total, first_percent), "first_booking", "platform"))
    if follow_up_percent and Booking.objects.filter(
        Q(completed_at__gte=now - FOLLOW_UP_WINDOW),
        client_id=client_id, doctor_id=doctor_id, status=Booking.Status.COMPLETED,
    ).exists():
        found.append(Discount(_percent_of(total, follow_up_percent), "follow_up", "doctor"))
    if service_prices:
        pkg = package_discount(client_id=client_id, doctor_id=doctor_id,
                               service_prices=service_prices, now=now)
        if pkg is not None:
            found.append(pkg)
    return found


def best_discount(*, client_id: UUID, doctor_id: UUID, total: Decimal, promo_code: str = "",
                  follow_up_percent: int = 0,
                  service_prices: dict[UUID, Decimal] | None = None) -> Discount:
    """Mijozga eng foydali BITTA chegirma. Noto'g'ri promo -> PromoInvalid (jim e'tiborsiz emas:
    mijoz kod ishladi deb o'ylab to'lab qo'ymasin)."""
    candidates = automatic_discounts(client_id=client_id, doctor_id=doctor_id, total=total,
                                     follow_up_percent=follow_up_percent,
                                     service_prices=service_prices)
    if promo_code:
        candidates.append(promo_discount(promo_code, client_id=client_id, doctor_id=doctor_id, total=total))
    if not candidates:
        return NONE
    best = max(candidates, key=lambda d: d.amount)
    return Discount(amount=min(best.amount, total), kind=best.kind, borne_by=best.borne_by,
                    promo=best.promo, enrollment=best.enrollment)


def release_redemption(booking_id: UUID) -> None:
    """Bron bekor / muddati o'tdi — promo limiti va paket seansi qaytadi."""
    from apps.booking.models import PackageUse

    PromoRedemption.objects.filter(booking_id=booking_id, is_active=True).update(is_active=False)
    PackageUse.objects.filter(booking_id=booking_id, is_active=True).update(is_active=False)
