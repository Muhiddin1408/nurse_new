"""
booking/payment_mode.py — B10: to'lov rejimi.

Hozirgi "avval to'la, 10 daqiqa" modeli O'zbekiston bozorida konversiyani
keskin tushiradi: mijozning katta qismi to'lov sahifasida to'xtaydi.

    to'liq oldindan  — uy chaqiruvi. Shifokor yo'lga chiqadi, kafolat shart
    zaklad (20%)     — qimmat qabullar. Qolgani klinikada
    klinikada        — oddiy klinika qabuli. Bron darhol tasdiqlanadi

REJIMNI MIJOZ TANLAMAYDI. Aks holda u har doim eng arzon yo'lni tanlar
va kafolat butunlay yo'qolardi. Rejim uch narsadan kelib chiqadi:
qabul joyi, shifokor sozlamasi va mijozning no-show tarixi.

Bu SOF FUNKSIYA (`resolve`) — bazaga tegmaydi, shuning uchun har bir
kombinatsiyani test bilan yopish arzon.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.conf import settings

from apps.booking.models import Booking

Mode = Booking.PaymentMode


@dataclass(frozen=True)
class PaymentPlan:
    mode: str
    prepay_amount: Decimal

    @property
    def is_paid_online(self) -> bool:
        return self.prepay_amount > 0


def resolve(
    *,
    total: Decimal,
    is_home_visit: bool,
    doctor_mode: str,
    client_no_shows: int,
    deposit_percent: int,
    force_prepaid_after: int,
) -> PaymentPlan:
    """Rejimni va onlayn to'lanadigan summani hisoblaydi."""
    # C12: 100% chegirma (bepul takroriy qabul) — onlayn to'lanadigan narsa yo'q.
    # 0 so'mlik checkout Payme/Click'da ochilmaydi; bron darhol tasdiqlanadi.
    if total <= 0:
        return PaymentPlan(Mode.AT_CLINIC, Decimal("0"))

    # ⬇ Uy chaqiruvi HAR DOIM to'liq oldindan. Shifokor shahar bo'ylab
    # yo'lga chiqadi — bu eng qimmat no-show turi va uni kafolatsiz
    # qoldirib bo'lmaydi. Shifokor sozlamasi bu qoidani bekor qila olmaydi.
    if is_home_visit:
        return PaymentPlan(Mode.PREPAID, total)

    # ⬇ No-show tarixi yomon mijoz uchun majburiy oldindan to'lov.
    # Klinikada to'lash imkoniyati — ishonch, va uni suiiste'mol qilgan
    # mijoz uni yo'qotadi. Aks holda bir necha mijoz butun rejimni
    # shifokorlar uchun foydasiz qilardi.
    if force_prepaid_after and client_no_shows >= force_prepaid_after:
        return PaymentPlan(Mode.PREPAID, total)

    if doctor_mode == Mode.AT_CLINIC:
        return PaymentPlan(Mode.AT_CLINIC, Decimal("0"))

    if doctor_mode == Mode.DEPOSIT:
        deposit = (total * Decimal(deposit_percent) / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        # Zaklad 0 bo'lib qolsa (juda arzon xizmat) — bu klinikada to'lash
        # bilan bir xil, lekin nomi boshqa bo'lardi. Halol nom qo'yamiz.
        if deposit <= 0:
            return PaymentPlan(Mode.AT_CLINIC, Decimal("0"))
        return PaymentPlan(Mode.DEPOSIT, deposit)

    return PaymentPlan(Mode.PREPAID, total)


def plan_from_settings(*, total: Decimal, is_home_visit: bool, doctor_mode: str, client_id: UUID):
    conf = settings.PAYMENT_MODES
    return resolve(
        total=total,
        is_home_visit=is_home_visit,
        doctor_mode=doctor_mode,
        client_no_shows=count_client_no_shows(client_id),
        deposit_percent=conf["deposit_percent"],
        force_prepaid_after=conf["force_prepaid_after_no_shows"],
    )


def count_client_no_shows(client_id: UUID) -> int:
    """Mijoz AYBI bilan bo'lgan kelmasliklar.

    `no_show_by` MUHIM: shifokor kelmagan bron ham `no_show` holatida
    bo'ladi, lekin unda mijozni jazolash noto'g'ri bo'lardi."""
    return Booking.objects.filter(
        client_id=client_id,
        status=Booking.Status.NO_SHOW,
        no_show_by=Booking.Actor.CLIENT,
    ).count()
