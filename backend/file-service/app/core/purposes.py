"""Fayl turlari (purpose): qaysi bucket, qanday format va hajm, qaysi servis biriktira oladi.

Yangi tur — shu yerga. `visibility=public` — anonim o'qiladigan `public-media` (rasm metadata'si,
jumladan GPS, tozalanadi); `private` — faqat ≤ 5 daqiqalik presigned URL va har ochilish auditga.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Visibility = Literal["public", "private"]

JPEG, PNG, WEBP, PDF = "image/jpeg", "image/png", "image/webp", "application/pdf"
IMAGE_TYPES = frozenset({JPEG, PNG, WEBP})
EXTENSIONS = {JPEG: ".jpg", PNG: ".png", WEBP: ".webp", PDF: ".pdf"}
MB = 1024 * 1024


@dataclass(frozen=True)
class Purpose:
    name: str
    visibility: Visibility
    content_types: frozenset[str]
    max_bytes: int
    owner_services: frozenset[str]


PURPOSES: dict[str, Purpose] = {
    p.name: p
    for p in (
        Purpose("avatar", "public", IMAGE_TYPES, 5 * MB, frozenset({"user-service"})),
        Purpose("doctor_photo", "public", IMAGE_TYPES, 5 * MB, frozenset({"catalog-service"})),
        Purpose("clinic_photo", "public", IMAGE_TYPES, 10 * MB, frozenset({"catalog-service"})),
        Purpose("doctor_document", "private", frozenset({PDF, JPEG, PNG}), 15 * MB, frozenset({"catalog-service"})),
        Purpose(
            "dispute_evidence", "private", frozenset({PDF, JPEG, PNG, WEBP}), 15 * MB, frozenset({"dispute-service"})
        ),
    )
}


def sniff(head: bytes) -> str | None:
    """Fayl boshidagi "sehrli" baytlar bo'yicha haqiqiy tur. Mijoz e'lon qilgan Content-Type ga ishonilmaydi."""
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if head.startswith(b"%PDF-"):
        return PDF
    return None
