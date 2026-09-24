"""
payments/fiscal.py — fiskal chek elementlari (B12).

Onlayn to'lov uchun chek: har bir xizmat — nomi, soni, narxi, QQS, MXIK (IKPU)
va o'lchov birligi kodi. Kodlar bron paytida `BookingItem` ga SNAPSHOT
qilinadi (`item_codes`), chek aynan shulardan quriladi.

Chek summasi TO'LOV summasiga teng bo'lishi shart. Zaklad (B10) rejimida
onlayn qism jami narxdan kichik — elementlar proporsional kamaytiriladi,
yaxlitlash qoldig'i oxirgi elementga yoziladi (tiyingacha aniq).

⚠️ Huquqiy talab — joriy formatni Payme/Click hujjati va soliq maslahatchisi
bilan tasdiqlang. Kod yo'q bo'lsa chek rad etiladi: `missing_codes()` bilan
monitoring qiling.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from django.conf import settings


def item_codes(service) -> dict:
    """ServiceDTO -> BookingItem snapshot maydonlari (default'lar bilan)."""
    f = settings.FISCAL
    vat = getattr(service, "vat_percent", None)
    return {
        "mxik_code": getattr(service, "mxik_code", "") or f["default_mxik_code"],
        "package_code": getattr(service, "package_code", "") or f["default_package_code"],
        "vat_percent": f["default_vat_percent"] if vat is None else vat,
    }


def receipt_items(payment) -> list[dict]:
    """[{title, price (so'm, Decimal), count, code, package_code, vat_percent}], summasi == payment.amount."""
    items = list(payment.booking.items.all().order_by("created_at", "id"))
    if not items:
        return []
    total = sum((i.price for i in items), Decimal("0"))
    ratio = (payment.amount / total) if total else Decimal("0")
    rows, running = [], Decimal("0")
    for idx, i in enumerate(items):
        if idx == len(items) - 1:
            price = payment.amount - running
        else:
            price = (i.price * ratio).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            running += price
        rows.append({
            "title": i.service_name[:128],
            "price": price,
            "count": 1,
            "code": i.mxik_code,
            "package_code": i.package_code,
            "vat_percent": i.vat_percent,
        })
    return rows


def vat_amount(price: Decimal, vat_percent: int) -> Decimal:
    """Narx ichidagi QQS: price * p / (100 + p)."""
    if not vat_percent:
        return Decimal("0")
    return (price * vat_percent / (100 + vat_percent)).quantize(Decimal("0.01"))


def missing_codes(payment) -> bool:
    return any(not r["code"] or not r["package_code"] for r in receipt_items(payment))


def payme_detail(payment) -> dict:
    """Payme `CheckPerformTransaction` javobidagi `detail` (narxlar tiyinda)."""
    return {
        "receipt_type": 0,
        "items": [
            {
                "title": r["title"],
                "price": int(r["price"] * 100),
                "count": r["count"],
                "code": r["code"],
                "package_code": r["package_code"],
                "vat_percent": r["vat_percent"],
            }
            for r in receipt_items(payment)
        ],
    }


def click_ofd_items(payment) -> list[dict]:
    """Click `payment/ofd_data/submit_items` elementlari (narxlar tiyinda)."""
    return [
        {
            "Name": r["title"],
            "SPIC": r["code"],
            "PackageCode": r["package_code"],
            "Price": int(r["price"] * 100),
            "Amount": r["count"],
            "VAT": int(vat_amount(r["price"], r["vat_percent"]) * 100),
            "VATPercent": r["vat_percent"],
            "CommissionInfo": {"TIN": settings.FISCAL["tin"]},
        }
        for r in receipt_items(payment)
    ]
