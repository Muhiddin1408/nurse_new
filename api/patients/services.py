"""
patients/services.py — bemor va manzil modulining TASHQI INTERFEYSI.

EGALIK LOADER'LARI (A1, A4):
    So'rovdan kelgan `patient_id` / `address_id` faqat shu funksiyalar orqali
    obyektga aylanadi. Ular egalikni tekshiradi va "topilmadi" hamda "begona"
    holatlari uchun BIR XIL natija (`None`) qaytaradi — javob kodidan boshqa
    odamning bemori mavjudligini bilib bo'lmasligi kerak.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from apps.account.models import Address, Patient


@dataclass(frozen=True)
class PatientDTO:
    id: UUID
    owner_id: UUID
    full_name: str
    birth_date: date
    gender: str


@dataclass(frozen=True)
class AddressDTO:
    id: UUID
    user_id: UUID
    city: str
    latitude: Decimal
    longitude: Decimal


def get_owned_patient(client_id: UUID, patient_id: UUID) -> PatientDTO | None:
    p = Patient.objects.filter(id=patient_id, owner_id=client_id, is_deleted=False).first()
    if p is None:
        return None
    return PatientDTO(
        id=p.id,
        owner_id=p.owner_id,
        full_name=p.full_name,
        birth_date=p.birth_date,
        gender=p.gender,
    )


def get_owned_address(client_id: UUID, address_id: UUID) -> AddressDTO | None:
    a = Address.objects.filter(id=address_id, user_id=client_id, is_deleted=False).first()
    if a is None:
        return None
    return AddressDTO(
        id=a.id,
        user_id=a.user_id,
        city=a.city,
        latitude=a.latitude,
        longitude=a.longitude,
    )
