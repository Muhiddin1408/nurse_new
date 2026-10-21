"""Fiskal chek elementlari (B12) — provayder OFD'si orqali (2026-10-21 qarori, ARCHITECTURE 14 #2):
Payme chekni `CheckPerformTransaction.detail` dan o'zi chiqaradi; Click'ga to'lovdan KEYIN
`ofd_data/submit_items` yuboriladi (scheduler, retry). Naqd/klinikada to'lov — chekni klinika kassasi chiqaradi.

Elementlar (nomi, narxi, MXIK/IKPU, o'lchov birligi, QQS) checkout paytida booking `/payable` dan SNAPSHOT
qilinadi (bo'sh kodlarni booking admin standarti bilan to'ldiradi). Chek summasi TO'LOV summasiga teng
bo'lishi shart: zaklad rejimida elementlar proporsional kamaytiriladi (pastga), qoldiq oxirgi elementga.

⚠️ Huquqiy talab — formatni Payme/Click joriy hujjati va soliq maslahatchisi bilan tasdiqlang.
"""

from __future__ import annotations

from typing import Any

from medbron_common.money import prorate


def receipt_items(items: list[dict[str, Any]], amount_tiyin: int) -> list[dict[str, Any]]:
    """booking `/payable.items` → [{title, price_tiyin, count, code, package_code, vat_percent}], Σprice == amount."""
    if not items:
        return []
    total = sum(int(i["price_tiyin"]) for i in items)
    rows: list[dict[str, Any]] = []
    running = 0
    for idx, i in enumerate(items):
        if idx == len(items) - 1:
            price = amount_tiyin - running
        else:
            price = int(i["price_tiyin"]) * amount_tiyin // total if total else 0
            running += price
        rows.append(
            {
                "title": str(i["service_name"])[:128],
                "price_tiyin": price,
                "count": 1,
                "code": i["mxik_code"],
                "package_code": i["package_code"],
                "vat_percent": int(i["vat_percent"]),
            }
        )
    return rows


def missing_codes(rows: list[dict[str, Any]]) -> bool:
    """Kod yo'q bo'lsa provayder chekni rad etadi — monitoring uchun."""
    return any(not r["code"] or not r["package_code"] for r in rows)


def vat_tiyin(price_tiyin: int, vat_percent: int) -> int:
    """Narx ichidagi QQS: price × p / (100 + p)."""
    return prorate(price_tiyin, vat_percent, 100 + vat_percent) if vat_percent else 0


def payme_detail(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "receipt_type": 0,
        "items": [
            {
                "title": r["title"],
                "price": r["price_tiyin"],
                "count": r["count"],
                "code": r["code"],
                "package_code": r["package_code"],
                "vat_percent": r["vat_percent"],
            }
            for r in rows
        ],
    }


def click_ofd_items(rows: list[dict[str, Any]], tin: str) -> list[dict[str, Any]]:
    return [
        {
            "Name": r["title"],
            "SPIC": r["code"],
            "PackageCode": r["package_code"],
            "Price": r["price_tiyin"],
            "Amount": r["count"],
            "VAT": vat_tiyin(r["price_tiyin"], r["vat_percent"]),
            "VATPercent": r["vat_percent"],
            "CommissionInfo": {"TIN": tin},
        }
        for r in rows
    ]
