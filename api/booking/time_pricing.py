"""
booking/time_pricing.py — dinamik narx (C12).

Kam talab vaqtlaridagi slotlarni to'ldirish uchun shifokor narx qoidasi
qo'yadi: "dushanba–juma 08:00–10:00 — 15% arzon". Qoida `catalog.PriceRule`
da, bu yerda faqat QO'LLASH mantig'i.

MUHIM — bu chegirma EMAS:
    chegirma   (`discounts.py`)  to'liq narx ustidan beriladi, kim to'lashi
                                 yoziladi (platforma / shifokor), payout'ga
                                 ta'sir qiladi va bekor qilinsa qaytariladi;
    narx tuzatmasi (shu fayl)    XIZMATNING O'SHA SLOTDAGI NARXI. U
                                 `BookingItem.price` snapshot'iga kiradi,
                                 `original_price` shundan yig'iladi, va
                                 faqat shundan keyin chegirma qidiriladi.

Shu ajratish bo'lmasa "20% chegirma" ikki marta hisoblanib ketardi —
avval qoidada, keyin promo kodda.

Tuzatma SLOT VAQTIGA qaraydi, bron qilingan vaqtga emas: mijoz kechasi
bron qilsa ham ertalabki slot ertalabki narxda qoladi.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

CENT = Decimal("0.01")
# Xavfsizlik chegarasi: admin panelidagi xato (masalan -900) narxni
# manfiyga aylantirmasin.
MIN_PERCENT, MAX_PERCENT = -90, 100


@dataclass(frozen=True)
class AdjustedService:
    """`catalog.ServiceDTO` ning narxi tuzatilgan nusxasi — faqat bronga
    yoziladigan maydonlar. DTO'ning o'zini o'zgartirmaymiz: u frozen va
    katalog kontrakti."""

    id: UUID
    price: Decimal


def clamp(percent: int) -> int:
    return max(MIN_PERCENT, min(MAX_PERCENT, int(percent)))


def adjust_price(price: Decimal, percent: int) -> Decimal:
    """Bitta narxga tuzatma. 0 bo'lsa narx BITTA TIYINGA ham o'zgarmaydi
    (yumaloqlash artefakti bo'lmasin)."""
    percent = clamp(percent)
    if not percent:
        return Decimal(price)
    adjusted = Decimal(price) * (100 + percent) / 100
    return max(adjusted.quantize(CENT, rounding=ROUND_HALF_UP), Decimal("0"))


def adjusted_prices(services, percent: int) -> dict[UUID, Decimal]:
    """`{service_id: tuzatilgan narx}` — chaqiruvchi shu bo'yicha item
    snapshot'ini va `original_price` ni yig'adi."""
    return {s.id: adjust_price(s.price, percent) for s in services}


def percent_for_slot(doctor_id: UUID, slot_start_at, local_tz) -> int:
    """Slot boshlanadigan MAHALLIY vaqt uchun tuzatma foizi (0 = qoidasiz)."""
    from api.catalog import services as catalog_services

    return clamp(catalog_services.get_price_adjust_percent(doctor_id, slot_start_at.astimezone(local_tz)))
