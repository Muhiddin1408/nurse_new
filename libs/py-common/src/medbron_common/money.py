"""Pul hisobi — tiyin (butun son), yaxlitlash ROUND_HALF_UP (ADR-0007, ADR-0011). Float ishlatilmaydi.

percent_of(15_000_000, 20)   -> 3_000_000       # 20% chegirma
bp_of(15_000_000, 1500)      -> 2_250_000       # 15.00% komissiya (bazis punkt)
adjust(10_000_001, -15)      -> 8_500_001       # slot narx foizi (C12), 0 dan kichik bo'lmaydi
"""

from __future__ import annotations


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
