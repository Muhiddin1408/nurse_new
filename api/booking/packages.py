"""
booking/packages.py — paketlar (C12).

Paket = shifokorning kurs taklifi: "5 ta muolaja, har biri 10% arzon,
90 kun ichida". Surunkali kasallikda (fizioterapiya, massaj, EKG nazorati)
standart amaliyot va qaytib kelishning eng kuchli sababi.

NEGA OLDINDAN TO'LOV EMAS:
    `Payment` bu tizimda bronga bog'langan (OneToOne, Payme/Click summa
    tekshiruvi `booking.prepay_amount` ga qaraydi). Paketni alohida mahsulot
    sifatida sotish uchun to'lov obyektini bronsiz qilish, refund va
    reconciliation (B7) ni ikkinchi turdagi obyektga kengaytirish kerak
    bo'lardi. Buning o'rniga mijoz paketga YOZILADI (pul o'tmaydi), va
    keyingi `sessions` ta broni chegirmali bo'ladi.

    Natija bir xil (kurs arzonroq), lekin ishlatilmagan seans — qaytariladigan
    pul emas, shunchaki ishlatilmagan huquq. Ya'ni bu xususiyat pul
    yo'qotishi mumkin bo'lgan yuzani umuman kengaytirmaydi.

Chegirmani shifokor ko'taradi (`borne_by=doctor`): bu uning taklifi,
platforma marketingi emas (B8 taqsimotida payout'dan chiqadi).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from api.catalog import services as catalog_services
from apps.booking.models import PackageEnrollment


class PackageError(Exception):
    """Paketga yozib bo'lmadi (400) — sababi matnda."""


@dataclass(frozen=True)
class EnrollmentDTO:
    id: UUID
    doctor_id: UUID
    service_id: UUID
    service_name: str
    package_name: str
    discount_percent: int
    sessions_total: int
    sessions_left: int
    expires_at: object


def _dto(enr: PackageEnrollment, *, service_name: str = "", package_name: str = "") -> EnrollmentDTO:
    return EnrollmentDTO(
        id=enr.id,
        doctor_id=enr.doctor_id,
        service_id=enr.service_id,
        service_name=service_name or enr.service.name,
        package_name=package_name or enr.package.name,
        discount_percent=enr.discount_percent,
        sessions_total=enr.sessions_total,
        sessions_left=enr.sessions_left,
        expires_at=enr.expires_at,
    )


def enroll(*, client_id: UUID, package_id: UUID) -> EnrollmentDTO:
    """Mijozni paketga yozadi. Muddat AYNAN SHU PAYTDAN boshlanadi —
    shifokorga bog'liq bo'lmagan aniq sana mijoz uchun ham tushunarli."""
    offer = catalog_services.get_package(package_id)
    if offer is None:
        raise PackageError("Paket topilmadi yoki faol emas")

    now = timezone.now()
    _expire_stale(client_id, now=now)
    try:
        with transaction.atomic():
            enr = PackageEnrollment.objects.create(
                package_id=offer.id,
                client_id=client_id,
                doctor_id=offer.doctor_id,
                service_id=offer.service_id,
                # SNAPSHOT: shifokor ertaga foizni pasaytirsa ham boshlangan
                # kurs o'z shartida qoladi.
                sessions_total=offer.sessions,
                discount_percent=offer.discount_percent,
                expires_at=now + timedelta(days=offer.validity_days),
            )
    except IntegrityError as exc:
        # `uniq_active_package_per_service` — bir xizmatga ikkita faol kurs
        # bo'lsa, qaysi biridan seans yechilgani noaniq bo'lib qolardi.
        raise PackageError("Bu xizmat bo'yicha faol paketingiz allaqachon bor") from exc
    return _dto(enr, service_name=offer.service_name, package_name=offer.name)


def my_enrollments(client_id: UUID) -> list[EnrollmentDTO]:
    now = timezone.now()
    _expire_stale(client_id, now=now)
    qs = (
        PackageEnrollment.objects.filter(client_id=client_id, is_active=True, expires_at__gt=now)
        .select_related("service", "package")
        .order_by("expires_at")
    )
    return [_dto(e) for e in qs]


def _expire_stale(client_id: UUID, *, now=None) -> None:
    """Muddati o'tgan kurslarni yopadi. Cron emas, o'qish paytida — kurslar
    soni kichik va bu yagona joy: yopilmagan kurs `uniq_active_package_per_service`
    tufayli mijozni yangi paketga yozilishdan to'sib qo'yardi."""
    now = now or timezone.now()
    PackageEnrollment.objects.filter(
        client_id=client_id, is_active=True, expires_at__lte=now
    ).update(is_active=False)


def close_exhausted(enrollment_id: UUID) -> None:
    """Seanslari tugagan kursni yopadi — mijoz shu xizmatga yangi paket
    ola oladi. Bron bekor qilinsa seans qaytadi (`PackageUse.is_active`),
    shuning uchun yopish faqat shu yerdan, hisoblab tekshirib qilinadi."""
    enr = PackageEnrollment.objects.filter(id=enrollment_id, is_active=True).first()
    if enr is not None and enr.sessions_left <= 0:
        PackageEnrollment.objects.filter(id=enr.id).update(is_active=False)
