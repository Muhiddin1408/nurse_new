"""Pul hisobi — tiyin (butun son), yaxlitlash ROUND_HALF_UP (ADR-0007, ADR-0011). Float ishlatilmaydi.

percent_of(15_000_000, 20)   -> 3_000_000       # 20% chegirma
bp_of(15_000_000, 1500)      -> 2_250_000       # 15.00% komissiya (bazis punkt)
adjust(10_000_001, -15)      -> 8_500_001       # slot narx foizi (C12), 0 dan kichik bo'lmaydi
share_of(money, 4_000_000)   -> Share(...)      # bron money_split'idan to'lov/refund ulushlari (ADR-0012)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _div_half_up(numerator: int, denominator: int) -> int:
    """Musbat va manfiy sonlar uchun 0.5 noldan uzoqqa yaxlitlanadi (Decimal ROUND_HALF_UP bilan bir xil)."""
    if denominator <= 0:
        raise ValueError("denominator > 0 bo'lishi kerak")
    sign = -1 if numerator < 0 else 1
    q, r = divmod(abs(numerator), denominator)
    return sign * (q + (1 if 2 * r >= denominator else 0))


def percent_of(amount_tiyin: int, percent: int) -> int:
    return _div_half_up(amount_tiyin * percent, 100)


def bp_of(amount_tiyin: int, basis_points: int) -> int:
    """1 bp = 0.01%. 1500 bp = 15%."""
    return _div_half_up(amount_tiyin * basis_points, 10_000)


def adjust(amount_tiyin: int, percent: int) -> int:
    """Narxni foizga o'zgartirish (−90..+100). 0% — o'zgarishsiz; natija ≥ 0."""
    if percent == 0:
        return amount_tiyin
    return max(0, _div_half_up(amount_tiyin * (100 + percent), 100))


def tiyin_to_som(amount_tiyin: int) -> str:
    """Ko'rsatish uchun: 12_500_050 -> "125 000.50"."""
    som, tiyin = divmod(amount_tiyin, 100)
    text = f"{som:,}".replace(",", " ")
    return f"{text}.{tiyin:02d}" if tiyin else text


def prorate(part_tiyin: int, amount_tiyin: int, total_tiyin: int) -> int:
    """`part × amount / total` (half-up). `total == 0` — 0."""
    if total_tiyin <= 0:
        return 0
    return _div_half_up(part_tiyin * amount_tiyin, total_tiyin)


@dataclass(frozen=True)
class Share:
    """Summa (to'lov yoki refund) bron `money_split`'i bo'yicha taqsimoti. doctor + platform + provider == amount."""

    doctor_tiyin: int
    platform_tiyin: int
    provider_tiyin: int


def share_of(money: Mapping[str, Any], amount_tiyin: int) -> Share:
    """payment ledger'i va billing payout'i uchun YAGONA formula (ADR-0012).

    To'liq summa — snapshot'ning o'zi; qismi (zaklad, qisman refund) — proporsional, yaxlitlash qoldig'i
    provayder ulushiga. Refundda provayder komissiyasi qaytmaydi — uni platforma ko'taradi
    (`platform_tiyin + provider_tiyin` platforma daromadidan yechiladi), shifokor faqat o'z ulushini qaytaradi.
    """
    total = int(money["total_tiyin"])
    if amount_tiyin == total:
        doctor, platform = int(money["doctor_payout_tiyin"]), int(money["platform_fee_tiyin"])
    else:
        doctor = prorate(int(money["doctor_payout_tiyin"]), amount_tiyin, total)
        platform = prorate(int(money["platform_fee_tiyin"]), amount_tiyin, total)
    return Share(doctor_tiyin=doctor, platform_tiyin=platform, provider_tiyin=amount_tiyin - doctor - platform)
