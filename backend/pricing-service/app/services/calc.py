"""Narx hisobi — SOF funksiyalar (baza va tarmoqsiz), monolit qoidalarining tiyindagi aniq ko'chirmasi.

Tartib (monolit `create_booking`, C12):
  1. Xizmat narxi → slot foizi (`price_adjust_percent`, −90..+100) HAR XIZMATGA alohida (chegirma emas).
  2. BITTA eng katta chegirma (qo'shilmaydi): first_booking | follow_up | package | promo; ≤ original.
  3. Komissiya taqsimoti (B8): stavkalar bron paytida muzlatiladi, yaxlitlash qoldig'i shifokorga.
  4. To'lov rejimi (B10): prepaid | deposit | at_clinic — mijoz tanlamaydi.
Yaxlitlash — ROUND_HALF_UP (`medbron_common.money`), tiyin.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from medbron_common.money import adjust, bp_of, percent_of

PROMO, FIRST_BOOKING, FOLLOW_UP, PACKAGE = "promo", "first_booking", "follow_up", "package"
PLATFORM, DOCTOR = "platform", "doctor"
PREPAID, DEPOSIT, AT_CLINIC = "prepaid", "deposit", "at_clinic"
MIN_ADJUST, MAX_ADJUST = -90, 100


@dataclass(frozen=True)
class Discount:
    kind: str
    amount_tiyin: int
    borne_by: str
    promo_id: uuid.UUID | None = None
    promo_code: str = ""
    enrollment_id: uuid.UUID | None = None


NO_DISCOUNT = Discount(kind="", amount_tiyin=0, borne_by="")


@dataclass(frozen=True)
class Split:
    total_tiyin: int
    platform_fee_tiyin: int
    provider_fee_tiyin: int
    doctor_payout_tiyin: int


@dataclass(frozen=True)
class PaymentPlan:
    mode: str
    prepay_tiyin: int


class PromoInvalid(Exception):
    """`reason`: not_found | expired | not_for_doctor | min_amount | limit_reached | already_used |
    first_booking_only."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def clamp_adjust(percent: int) -> int:
    return max(MIN_ADJUST, min(MAX_ADJUST, percent))


def adjusted_prices(base_prices: dict[uuid.UUID, int], percent: int) -> dict[uuid.UUID, int]:
    pct = clamp_adjust(percent)
    return {sid: adjust(price, pct) for sid, price in base_prices.items()}


@dataclass(frozen=True)
class PromoTerms:
    id: uuid.UUID
    code: str
    kind: str  # percent | fixed
    value: int
    max_discount_tiyin: int | None
    min_amount_tiyin: int
    valid_from: datetime | None
    valid_until: datetime | None
    max_uses: int | None
    max_uses_per_user: int
    first_booking_only: bool
    doctor_id: uuid.UUID | None
    borne_by: str


@dataclass(frozen=True)
class PromoUsage:
    total_live: int
    client_live: int


def promo_discount(
    promo: PromoTerms | None,
    *,
    original_tiyin: int,
    doctor_id: uuid.UUID,
    has_previous_booking: bool,
    usage: PromoUsage,
    now: datetime,
) -> Discount:
    """Monolit `promo_discount` tekshiruv tartibi aynan saqlangan."""
    if promo is None:
        raise PromoInvalid("not_found", "Promo kod topilmadi")
    if (promo.valid_from and now < promo.valid_from) or (promo.valid_until and now > promo.valid_until):
        raise PromoInvalid("expired", "Promo kod muddati tugagan")
    if promo.doctor_id is not None and promo.doctor_id != doctor_id:
        raise PromoInvalid("not_for_doctor", "Promo kod bu shifokor uchun emas")
    if original_tiyin < promo.min_amount_tiyin:
        raise PromoInvalid("min_amount", f"Promo kod {promo.min_amount_tiyin // 100} so'mdan boshlab ishlaydi")
    if promo.max_uses is not None and usage.total_live >= promo.max_uses:
        raise PromoInvalid("limit_reached", "Promo kod limiti tugagan")
    if usage.client_live >= promo.max_uses_per_user:
        raise PromoInvalid("already_used", "Bu promo koddan allaqachon foydalangansiz")
    if promo.first_booking_only and has_previous_booking:
        raise PromoInvalid("first_booking_only", "Promo kod faqat birinchi bron uchun")
    amount = percent_of(original_tiyin, promo.value) if promo.kind == "percent" else promo.value
    if promo.max_discount_tiyin is not None:
        amount = min(amount, promo.max_discount_tiyin)
    return Discount(
        kind=PROMO,
        amount_tiyin=min(amount, original_tiyin),
        borne_by=promo.borne_by,
        promo_id=promo.id,
        promo_code=promo.code,
    )


@dataclass(frozen=True)
class EnrollmentOffer:
    enrollment_id: uuid.UUID
    service_id: uuid.UUID
    discount_percent: int
    sessions_left: int


def package_discount(offers: list[EnrollmentOffer], prices: dict[uuid.UUID, int]) -> Discount:
    """Birinchi mos yozilish (muddati yaqinroq): foiz faqat O'SHA xizmatning (slot foizi qo'llangan) narxiga."""
    for offer in offers:
        if offer.sessions_left <= 0 or offer.service_id not in prices:
            continue
        amount = percent_of(prices[offer.service_id], offer.discount_percent)
        if amount > 0:
            return Discount(kind=PACKAGE, amount_tiyin=amount, borne_by=DOCTOR, enrollment_id=offer.enrollment_id)
    return NO_DISCOUNT


@dataclass
class DiscountInputs:
    original_tiyin: int
    prices: dict[uuid.UUID, int]
    first_booking_percent: int = 0
    has_previous_booking: bool = True
    follow_up_percent: int = 0
    has_recent_completed_with_doctor: bool = False
    package_offers: list[EnrollmentOffer] = field(default_factory=list)
    promo: Discount | None = None


def best_discount(inputs: DiscountInputs) -> Discount:
    """Chegirmalar qo'shilmaydi: eng kattasi (teng bo'lsa — ro'yxatdagi birinchisi), ≤ original."""
    candidates: list[Discount] = []
    if inputs.first_booking_percent > 0 and not inputs.has_previous_booking:
        candidates.append(
            Discount(FIRST_BOOKING, percent_of(inputs.original_tiyin, inputs.first_booking_percent), PLATFORM)
        )
    if inputs.follow_up_percent > 0 and inputs.has_recent_completed_with_doctor:
        candidates.append(Discount(FOLLOW_UP, percent_of(inputs.original_tiyin, inputs.follow_up_percent), DOCTOR))
    package = package_discount(inputs.package_offers, inputs.prices)
    if package.amount_tiyin > 0:
        candidates.append(package)
    if inputs.promo is not None and inputs.promo.amount_tiyin > 0:
        candidates.append(inputs.promo)
    candidates = [c for c in candidates if c.amount_tiyin > 0]
    if not candidates:
        return NO_DISCOUNT
    best = max(candidates, key=lambda d: d.amount_tiyin)
    if best.amount_tiyin > inputs.original_tiyin:
        return Discount(
            best.kind, inputs.original_tiyin, best.borne_by, best.promo_id, best.promo_code, best.enrollment_id
        )
    return best


def split_amount(total_tiyin: int, commission_bp: int, provider_fee_bp: int) -> Split:
    platform = bp_of(total_tiyin, commission_bp)
    provider = bp_of(total_tiyin, provider_fee_bp)
    return Split(total_tiyin, platform, provider, total_tiyin - platform - provider)


def split_with_discount(original_tiyin: int, discount: Discount, commission_bp: int, provider_fee_bp: int) -> Split:
    """Platforma ko'targan chegirmada shifokor chegirmasiz narxdagi ulushini oladi; `platform_fee` manfiy
    bo'lishi mumkin (marketing xarajati). Invariant: platform + provider + payout == total."""
    total = original_tiyin - discount.amount_tiyin
    if discount.amount_tiyin <= 0 or discount.borne_by != PLATFORM:
        return split_amount(total, commission_bp, provider_fee_bp)
    payout = split_amount(original_tiyin, commission_bp, provider_fee_bp).doctor_payout_tiyin
    provider = bp_of(total, provider_fee_bp)
    return Split(total, total - provider - payout, provider, payout)


def resolve_payment_mode(
    *,
    total_tiyin: int,
    is_home_visit: bool,
    doctor_mode: str,
    client_no_shows: int,
    deposit_percent: int,
    force_prepaid_after_no_shows: int,
) -> PaymentPlan:
    """Monolit `payment_mode.resolve` tartibi."""
    if total_tiyin <= 0:
        return PaymentPlan(AT_CLINIC, 0)  # 0 so'mlik checkout Payme/Click'da ochilmaydi
    if is_home_visit:
        return PaymentPlan(PREPAID, total_tiyin)
    if force_prepaid_after_no_shows and client_no_shows >= force_prepaid_after_no_shows:
        return PaymentPlan(PREPAID, total_tiyin)
    if doctor_mode == AT_CLINIC:
        return PaymentPlan(AT_CLINIC, 0)
    if doctor_mode == DEPOSIT:
        deposit = percent_of(total_tiyin, deposit_percent)
        return PaymentPlan(DEPOSIT, deposit) if deposit > 0 else PaymentPlan(AT_CLINIC, 0)
    return PaymentPlan(PREPAID, total_tiyin)
