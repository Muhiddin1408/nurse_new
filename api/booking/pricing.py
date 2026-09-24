"""
booking/pricing.py — pul taqsimoti (B8). Sof funksiya.

    total_price        150 000   mijoz to'laydi
    platform_fee        22 500   komissiya (15%)
    provider_fee         1 500   Payme ushlab qoladi (1%)
    doctor_payout      126 000   shifokorga

Stavkalar bron paytida MUZLATILADI (`commission_rate`, `provider_fee_rate`
bron qatorida saqlanadi). Ertaga foiz o'zgarsa, eski bronlar o'zgarmaydi —
xuddi `BookingItem` narx snapshot'i kabi.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class MoneySplit:
    total_price: Decimal
    commission_rate: Decimal
    provider_fee_rate: Decimal
    platform_fee: Decimal
    provider_fee: Decimal
    doctor_payout: Decimal


def _money(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def split_amount(total: Decimal, commission_rate: Decimal, provider_fee_rate: Decimal) -> MoneySplit:
    total = _money(total)
    platform_fee = _money(total * Decimal(commission_rate))
    provider_fee = _money(total * Decimal(provider_fee_rate))
    return MoneySplit(
        total_price=total,
        commission_rate=Decimal(commission_rate),
        provider_fee_rate=Decimal(provider_fee_rate),
        platform_fee=platform_fee,
        provider_fee=provider_fee,
        # qoldiq shifokorga — yuvarlash tiyini hech qachon yo'qolmaydi
        doctor_payout=total - platform_fee - provider_fee,
    )


def split_with_discount(original: Decimal, discount: Decimal, borne_by: str,
                        commission_rate: Decimal, provider_fee_rate: Decimal) -> MoneySplit:
    """C12: chegirmali taqsimot. Invariant: platform_fee + provider_fee + doctor_payout == total.

    platform — shifokor chegirmasiz narxdagi ulushini oladi; farqni platforma
               ko'taradi (`platform_fee` manfiy bo'lishi mumkin — marketing xarajati).
    doctor / chegirma yo'q — odatiy taqsimot chegirmali narxdan.
    """
    total = _money(Decimal(original) - Decimal(discount))
    if not discount or borne_by != "platform":
        return split_amount(total, commission_rate, provider_fee_rate)
    full = split_amount(original, commission_rate, provider_fee_rate)
    provider_fee = _money(total * Decimal(provider_fee_rate))
    doctor_payout = full.doctor_payout
    return MoneySplit(
        total_price=total,
        commission_rate=Decimal(commission_rate),
        provider_fee_rate=Decimal(provider_fee_rate),
        platform_fee=total - provider_fee - doctor_payout,
        provider_fee=provider_fee,
        doctor_payout=doctor_payout,
    )


def current_rates(provider: str = "payme") -> tuple[Decimal, Decimal]:
    from django.conf import settings

    commission = Decimal(str(settings.PLATFORM_COMMISSION_RATE))
    provider_fee = Decimal(str(settings.PROVIDER_FEE_RATES.get(provider, 0)))
    return commission, provider_fee
