"""Profil, bemorlar, manzillar, sevimlilar va akkauntni o'chirish (ARCHITECTURE 6.10)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction
from medbron_common.events import Events

from app.clients.file import AttachedFile, FileClient, FileNotAttachable
from app.core.config import get_settings
from app.events import outbox
from app.models import Address, Favorite, Patient, Profile
from app.repositories import users as repo
from app.services.errors import (
    ActiveBookingsExist,
    AddressNotFound,
    DoctorAccountDeletion,
    InvalidAvatar,
    LimitReached,
    PatientNotFound,
    ProfileDeleted,
)

logger = logging.getLogger(__name__)
DELETED_NAME = "O'chirilgan"


async def get_me(session: AsyncSession, user_id: uuid.UUID) -> Profile:
    async with transaction(session):
        profile = await repo.ensure_profile(session, user_id)
    if profile.deleted_at is not None:
        raise ProfileDeleted()
    return profile


def _profile_event(session: AsyncSession, profile: Profile) -> None:
    outbox.add_event(
        session,
        Events.USER_PROFILE_UPDATED,
        aggregate_id=profile.user_id,
        data={
            "user_id": profile.user_id,
            "full_name": profile.full_name,
            "preferred_language": profile.preferred_language,
        },
    )


async def update_me(
    session: AsyncSession,
    files: FileClient,
    user_id: uuid.UUID,
    changes: dict[str, Any],
) -> Profile:
    """Ism/til o'zgarsa — `user.profile_updated` (notification kontakti). Avatar: file-service'da
    biriktiriladi (tekshiruv o'sha yerda), eskisi commit'dan keyin bo'shatiladi."""
    attached = None
    if changes.get("avatar_file_id"):
        try:
            attached = await files.attach_avatar(changes["avatar_file_id"], user_id)
        except FileNotAttachable as exc:
            raise InvalidAvatar(extra={"reason": exc.code}) from exc
    try:
        profile, old_avatar = await _apply_profile_changes(session, user_id, changes, attached)
    except ProfileDeleted:
        if attached is not None:
            await _release_quietly(files, attached.id, user_id)  # yetim fayl qolmasin
        raise
    if old_avatar is not None:
        await _release_quietly(files, old_avatar, user_id)
    return profile


async def _release_quietly(files: FileClient, file_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Bo'shatilmasa ham fayl ishlatilmaydi (profil boshqasiga ishora qiladi) — xato log orqali kuzatiladi."""
    try:
        await files.release_avatar(file_id, user_id)
    except Exception:
        logger.warning("avatar release failed", extra={"file_id": str(file_id)})


async def _apply_profile_changes(
    session: AsyncSession, user_id: uuid.UUID, changes: dict[str, Any], attached: AttachedFile | None
) -> tuple[Profile, uuid.UUID | None]:
    old_avatar: uuid.UUID | None = None
    async with transaction(session):
        profile = await repo.ensure_profile(session, user_id)
        if profile.deleted_at is not None:
            raise ProfileDeleted()
        before = (profile.full_name, profile.preferred_language)
        if "full_name" in changes and changes["full_name"] is not None:
            profile.full_name = changes["full_name"]
        if "preferred_language" in changes and changes["preferred_language"] is not None:
            profile.preferred_language = changes["preferred_language"]
        if "avatar_file_id" in changes:
            new_id = attached.id if attached else None
            if profile.avatar_file_id != new_id:
                old_avatar = profile.avatar_file_id
                profile.avatar_file_id = new_id
                profile.avatar_url = attached.url if attached else None
                profile.avatar_thumbnail_url = attached.thumbnail_url if attached else None
        if (profile.full_name, profile.preferred_language) != before:
            _profile_event(session, profile)
    return profile, old_avatar


async def request_deletion(
    session: AsyncSession, files: FileClient, user_id: uuid.UUID, *, roles: frozenset[str], ip: str | None
) -> None:
    """O'chirish = ANONIMLASHTIRISH: bronlar/to'lovlar qonuniy muddatgacha qoladi, lekin hech kimga
    bog'lanmaydi. `user.deletion_requested` → auth akkauntni faolsizlantiradi va raqamni bo'shatadi."""
    if "doctor" in roles:
        raise DoctorAccountDeletion()
    now = datetime.now(UTC)
    async with transaction(session):
        profile = await repo.ensure_profile(session, user_id)
        if profile.deleted_at is not None:
            return
        if await repo.active_bookings(session, user_id) > 0:
            raise ActiveBookingsExist()
        avatar = profile.avatar_file_id
        profile.full_name = ""
        profile.avatar_file_id = None
        profile.avatar_url = None
        profile.avatar_thumbnail_url = None
        profile.deleted_at = now
        await session.execute(
            update(Patient)
            .where(Patient.user_id == user_id)
            .values(full_name=DELETED_NAME, relation="", weight_kg=None, is_deleted=True)
        )
        await session.execute(
            update(Address)
            .where(Address.user_id == user_id)
            .values(
                label="", street="—", district="", entrance="", floor="", apartment="", comment="",
                is_default=False, is_deleted=True,
            )
        )  # fmt: skip
        await session.execute(delete(Favorite).where(Favorite.user_id == user_id))
        outbox.add_event(
            session,
            Events.USER_DELETION_REQUESTED,
            aggregate_id=user_id,
            data={"user_id": user_id, "requested_at": now},
        )
        record_audit(
            outbox,
            session,
            Actor("user", user_id, "client"),
            action="user.delete",
            object_type="user",
            object_id=user_id,
            ip=ip,
        )
    if avatar is not None:
        await _release_quietly(files, avatar, user_id)


# --- patients --------------------------------------------------------------------------------


async def create_patient(session: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]) -> Patient:
    async with transaction(session):
        if await repo.count_patients(session, user_id) >= get_settings().max_patients_per_user:
            raise LimitReached("Bemorlar soni chegarasiga yetildi")
        patient = Patient(id=uuid.uuid4(), user_id=user_id, **data)
        session.add(patient)
    return patient


async def update_patient(
    session: AsyncSession, user_id: uuid.UUID, patient_id: uuid.UUID, changes: dict[str, Any]
) -> Patient:
    async with transaction(session):
        patient = await repo.get_owned_patient(session, user_id, patient_id, for_update=True)
        if patient is None:
            raise PatientNotFound()
        for key, value in changes.items():
            setattr(patient, key, value)
    return patient


async def delete_patient(session: AsyncSession, user_id: uuid.UUID, patient_id: uuid.UUID) -> None:
    """Faol broni bor — 409; bronlar tarixi bor — yumshoq o'chirish; hech qachon bron qilinmagan — DELETE."""
    async with transaction(session):
        patient = await repo.get_owned_patient(session, user_id, patient_id, for_update=True)
        if patient is None:
            raise PatientNotFound()
        active, total = await repo.patient_bookings(session, patient_id)
        if active:
            raise ActiveBookingsExist("Bemorning faol bronlari bor — avval ularni bekor qiling")
        if total:
            patient.is_deleted = True
        else:
            await session.delete(patient)


# --- addresses -------------------------------------------------------------------------------


async def create_address(session: AsyncSession, user_id: uuid.UUID, data: dict[str, Any]) -> Address:
    async with transaction(session):
        if await repo.count_addresses(session, user_id) >= get_settings().max_addresses_per_user:
            raise LimitReached("Manzillar soni chegarasiga yetildi")
        address = Address(id=uuid.uuid4(), user_id=user_id, **data)
        if address.is_default:
            await repo.clear_default_address(session, user_id, keep=None)
        session.add(address)
    return address


async def update_address(
    session: AsyncSession, user_id: uuid.UUID, address_id: uuid.UUID, changes: dict[str, Any]
) -> Address:
    async with transaction(session):
        address = await repo.get_owned_address(session, user_id, address_id, for_update=True)
        if address is None:
            raise AddressNotFound()
        if changes.get("is_default"):
            await repo.clear_default_address(session, user_id, keep=address.id)
        for key, value in changes.items():
            setattr(address, key, value)
    return address


async def delete_address(session: AsyncSession, user_id: uuid.UUID, address_id: uuid.UUID) -> None:
    """Har doim yumshoq: bron o'z nusxasini saqlaydi, lekin manzil ID'si tarixda qolishi mumkin."""
    async with transaction(session):
        address = await repo.get_owned_address(session, user_id, address_id, for_update=True)
        if address is None:
            raise AddressNotFound()
        address.is_deleted = True
        address.is_default = False


# --- favorites -------------------------------------------------------------------------------


async def add_favorite(session: AsyncSession, user_id: uuid.UUID, doctor_id: uuid.UUID) -> None:
    async with transaction(session):
        if await repo.count_favorites(session, user_id) >= get_settings().max_favorites_per_user:
            raise LimitReached("Sevimlilar soni chegarasiga yetildi")
        await repo.add_favorite(session, user_id, doctor_id)


async def remove_favorite(session: AsyncSession, user_id: uuid.UUID, doctor_id: uuid.UUID) -> None:
    async with transaction(session):
        await repo.remove_favorite(session, user_id, doctor_id)
