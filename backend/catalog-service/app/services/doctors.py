"""Shifokor: onboarding (D2), moderatsiya holat mashinasi, ommaviy profil, litsenziya.

Holatlar: draft → pending (submit) → approved | rejected; rejected → (tuzatib) pending; approved ↔ suspended.
Har o'tish — qator qulfi ostida, o'sha holatga takror o'tish — idempotent (event qayta chiqmaydi).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.events import Events

from app.clients.file import FileClient, FileRejected
from app.core.config import get_settings
from app.events import outbox
from app.models import Doctor, DoctorDocument
from app.repositories import catalog as repo
from app.services.common import SYSTEM_ACTOR, emit_doctor_profile_updated
from app.services.errors import (
    DoctorNotApproved,
    DoctorNotFound,
    DocumentNotFound,
    InvalidFile,
    InvalidRequest,
    InvalidTransition,
    LicenseExpired,
    NotADoctor,
    NotEditable,
    SpecializationNotFound,
    SubmitIncomplete,
)

logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Asia/Tashkent")

EDITABLE = ("draft", "rejected")
REQUIRED_DOCUMENTS = ("diploma", "license")
ONBOARDING_FIELDS = (
    "full_name",
    "bio",
    "education",
    "experience_years",
    "languages",
    "license_number",
    "license_expires_at",
    "accepts_home_visits",
    "home_visit_radius_km",
)
PROFILE_FIELDS = (
    "bio",
    "education",
    "languages",
    "accepts_home_visits",
    "home_visit_radius_km",
    "follow_up_discount_percent",
    "home_base_latitude",
    "home_base_longitude",
    "clinic_payment_mode",
)


def today_local() -> date:
    return datetime.now(LOCAL_TZ).date()


async def require_doctor(session: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False) -> Doctor:
    doctor = await repo.doctor_by_user(session, user_id, for_update=for_update)
    if doctor is None:
        raise NotADoctor()
    return doctor


async def require_approved(session: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False) -> Doctor:
    """Holat DB'dan tekshiriladi — token'dagi rol faqat ishora (to'xtatilgan shifokor eski token bilan kira olmaydi)."""
    doctor = await require_doctor(session, user_id, for_update=for_update)
    if doctor.status != "approved":
        raise DoctorNotApproved()
    return doctor


# --- onboarding ------------------------------------------------------------------------------


async def start(session: AsyncSession, user_id: uuid.UUID) -> tuple[Doctor, bool]:
    """Idempotent: profil bor bo'lsa — o'sha (created=False)."""
    existing = await repo.doctor_by_user(session, user_id)
    if existing is not None:
        return existing, False
    doctor = Doctor(id=uuid.uuid4(), user_id=user_id, status="draft")
    session.add(doctor)
    await session.flush()
    return doctor, True


async def _check_specializations(session: AsyncSession, ids: list[uuid.UUID]) -> None:
    if await repo.count_existing_specializations(session, ids) != len(set(ids)):
        raise SpecializationNotFound()


async def update_onboarding(session: AsyncSession, user_id: uuid.UUID, changes: dict[str, Any]) -> Doctor:
    doctor = await require_doctor(session, user_id, for_update=True)
    if doctor.status not in EDITABLE:
        raise NotEditable()
    if "specialization_ids" in changes:
        ids = list(changes.pop("specialization_ids"))
        await _check_specializations(session, ids)
        await repo.set_specializations(session, doctor.id, ids)
    for key in ONBOARDING_FIELDS:
        if key in changes:
            setattr(doctor, key, changes[key])
    return doctor


async def add_document(
    session: AsyncSession, files: FileClient, user_id: uuid.UUID, kind: str, file_id: uuid.UUID
) -> DoctorDocument:
    doctor = await require_doctor(session, user_id, for_update=True)
    if doctor.status not in EDITABLE:
        raise NotEditable()
    doc_id = uuid.uuid4()
    try:
        attached = await files.attach(
            file_id, owner_user_id=user_id, owner_ref=f"doctor:{doctor.id}:document:{doc_id}", purpose="doctor_document"
        )
    except FileRejected as exc:
        raise InvalidFile(extra={"reason": exc.code}) from exc
    document = DoctorDocument(
        id=doc_id, doctor_id=doctor.id, kind=kind, file_id=attached.id, content_type=attached.content_type
    )
    session.add(document)
    return document


async def remove_document(session: AsyncSession, files: FileClient, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
    doctor = await require_doctor(session, user_id, for_update=True)
    if doctor.status not in EDITABLE:
        raise NotEditable()
    document = await session.get(DoctorDocument, document_id)
    if document is None or document.doctor_id != doctor.id:
        raise DocumentNotFound()
    await session.delete(document)
    await session.flush()
    try:
        await files.release(document.file_id, f"doctor:{doctor.id}:document:{document.id}")
    except Exception:
        logger.warning("document release failed", extra={"file_id": str(document.file_id)})


async def missing_requirements(session: AsyncSession, doctor: Doctor) -> list[str]:
    missing: list[str] = []
    if not doctor.full_name.strip():
        missing.append("full_name")
    if not await repo.specialization_ids(session, doctor.id):
        missing.append("specialization_ids")
    if not doctor.license_number.strip():
        missing.append("license_number")
    if doctor.license_expires_at is None:
        missing.append("license_expires_at")
    elif doctor.license_expires_at <= today_local():
        missing.append("license_expires_at:expired")
    kinds = {d.kind for d in await repo.documents(session, doctor.id)}
    missing.extend(f"document:{k}" for k in REQUIRED_DOCUMENTS if k not in kinds)
    return missing


async def submit(session: AsyncSession, user_id: uuid.UUID) -> Doctor:
    doctor = await require_doctor(session, user_id, for_update=True)
    if doctor.status == "pending":
        return doctor
    if doctor.status not in EDITABLE:
        raise InvalidTransition()
    missing = await missing_requirements(session, doctor)
    if missing:
        raise SubmitIncomplete(extra={"missing": missing})
    now = datetime.now(UTC)
    doctor.status, doctor.submitted_at, doctor.status_reason = "pending", now, ""
    outbox.add_event(
        session,
        Events.DOCTOR_ONBOARDING_SUBMITTED,
        aggregate_id=doctor.id,
        data={
            "doctor_id": doctor.id,
            "user_id": doctor.user_id,
            "submitted_at": now,
            "moderation_due_at": now + timedelta(hours=get_settings().moderation_sla_hours),
        },
    )
    return doctor


def sla_due_at(doctor: Doctor) -> datetime | None:
    if doctor.status != "pending" or doctor.submitted_at is None:
        return None
    return doctor.submitted_at + timedelta(hours=get_settings().moderation_sla_hours)


# --- ommaviy profil va litsenziya -----------------------------------------------------------


async def update_profile(
    session: AsyncSession, files: FileClient, user_id: uuid.UUID, changes: dict[str, Any]
) -> Doctor:
    """Tasdiqlangan shifokor — moderatsiyasiz maydonlar. Ism, mutaxassislik, litsenziya — moderatsiya orqali."""
    doctor = await require_approved(session, user_id, for_update=True)
    lat_set = "home_base_latitude" in changes
    lng_set = "home_base_longitude" in changes
    if lat_set != lng_set:
        raise InvalidRequest("home_base_latitude va home_base_longitude birga yuboriladi")
    if "photo_file_id" in changes:
        await _set_photo(files, doctor, changes.pop("photo_file_id"))
    for key in PROFILE_FIELDS:
        if key in changes:
            setattr(doctor, key, changes[key])
    await emit_doctor_profile_updated(session, doctor)
    return doctor


async def _set_photo(files: FileClient, doctor: Doctor, file_id: uuid.UUID | None) -> None:
    ref = f"doctor:{doctor.id}:photo"
    old = doctor.photo_file_id
    if file_id == old:
        return
    if file_id is None:
        doctor.photo_file_id, doctor.photo_url = None, None
    else:
        try:
            attached = await files.attach(file_id, owner_user_id=doctor.user_id, owner_ref=ref, purpose="doctor_photo")
        except FileRejected as exc:
            raise InvalidFile(extra={"reason": exc.code}) from exc
        doctor.photo_file_id, doctor.photo_url = attached.id, attached.url
    if old is not None:
        try:
            await files.release(old, ref)
        except Exception:
            logger.warning("old photo release failed", extra={"file_id": str(old)})


async def update_license(
    session: AsyncSession, user_id: uuid.UUID, license_number: str, expires_at: date, *, ip: str | None
) -> Doctor:
    """ADR-0011 #8: to'xtatilgan shifokor ham litsenziyani yangilaydi (monolitda bu mumkin emas edi — deadlock).
    Qayta faollashtirish — admin (`reinstate`)."""
    doctor = await require_doctor(session, user_id, for_update=True)
    if doctor.status not in ("approved", "suspended"):
        raise InvalidTransition("Onboarding paytida litsenziya profil orqali kiritiladi")
    if expires_at <= today_local():
        raise LicenseExpired("Yangi litsenziya muddati kelajakda bo'lishi kerak")
    before = {"license_number": doctor.license_number, "license_expires_at": str(doctor.license_expires_at)}
    doctor.license_number, doctor.license_expires_at = license_number, expires_at
    doctor.license_reminder_sent_at = None
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "doctor"),
        action="doctor.license.update",
        object_type="doctor",
        object_id=doctor.id,
        before=before,
        after={"license_number": license_number, "license_expires_at": str(expires_at)},
        ip=ip,
    )
    return doctor


# --- moderatsiya -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Moderator:
    actor: Actor
    actor_id: uuid.UUID  # event `*_by`


TRANSITIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "approve": (("pending",), "approved"),
    "reject": (("pending",), "rejected"),
    "suspend": (("approved",), "suspended"),
    "reinstate": (("suspended",), "approved"),
}


async def moderate(
    session: AsyncSession,
    doctor_id: uuid.UUID,
    action: str,
    moderator: Moderator,
    *,
    reason: str = "",
    ip: str | None = None,
) -> Doctor:
    sources, target = TRANSITIONS[action]
    doctor = await repo.get_doctor(session, doctor_id, for_update=True)
    if doctor is None:
        raise DoctorNotFound()
    if doctor.status == target:
        return doctor
    if doctor.status not in sources:
        raise InvalidTransition(extra={"status": doctor.status})
    if action in ("reject", "suspend") and not reason.strip():
        raise InvalidRequest("Sabab majburiy")
    if action in ("approve", "reinstate") and (
        doctor.license_expires_at is None or doctor.license_expires_at <= today_local()
    ):
        raise LicenseExpired()
    now = datetime.now(UTC)
    before = doctor.status
    doctor.status, doctor.status_reason = target, reason
    doctor.moderated_by, doctor.moderated_at = moderator.actor.actor_id, now
    base = {"doctor_id": doctor.id, "user_id": doctor.user_id}
    if action == "approve":
        outbox.add_event(
            session,
            Events.DOCTOR_APPROVED,
            aggregate_id=doctor.id,
            data={
                **base,
                "full_name": doctor.full_name,
                "specialization_ids": await repo.specialization_ids(session, doctor.id),
                "accepts_home_visits": doctor.accepts_home_visits,
                "approved_by": moderator.actor_id,
                "approved_at": now,
            },
        )
    elif action == "reject":
        outbox.add_event(
            session,
            Events.DOCTOR_REJECTED,
            aggregate_id=doctor.id,
            data={**base, "reason": reason, "rejected_by": moderator.actor_id},
        )
    elif action == "suspend":
        outbox.add_event(
            session,
            Events.DOCTOR_SUSPENDED,
            aggregate_id=doctor.id,
            data={**base, "reason": reason, "suspended_by": moderator.actor_id},
        )
    else:
        outbox.add_event(
            session,
            Events.DOCTOR_REINSTATED,
            aggregate_id=doctor.id,
            data={**base, "reinstated_by": moderator.actor_id},
        )
    record_audit(
        outbox,
        session,
        moderator.actor,
        action=f"doctor.status.{target}",
        object_type="doctor",
        object_id=doctor.id,
        before={"status": before},
        after={"status": target, "reason": reason},
        ip=ip,
    )
    return doctor


async def run_license_check(session: AsyncSession, *, today: date | None = None) -> dict[str, int]:
    """Kunlik: muddati o'tgan tasdiqlangan shifokor — to'xtatiladi; 30 kun qolgan — bir marta eslatma."""
    settings = get_settings()
    day = today or today_local()
    expired = (
        await session.scalars(select(Doctor.id).where(Doctor.status == "approved", Doctor.license_expires_at <= day))
    ).all()
    system = Moderator(Actor.system("license-check"), SYSTEM_ACTOR)
    for doctor_id in expired:
        await moderate(session, doctor_id, "suspend", system, reason="license_expired")
    horizon = day + timedelta(days=settings.license_reminder_days)
    reminded_rows = (
        await session.execute(
            update(Doctor)
            .where(
                Doctor.status == "approved",
                Doctor.license_expires_at > day,
                Doctor.license_expires_at <= horizon,
                Doctor.license_reminder_sent_at.is_(None),
            )
            .values(license_reminder_sent_at=datetime.now(UTC))
            .returning(Doctor.id, Doctor.user_id, Doctor.license_expires_at)
        )
    ).all()
    for doctor_id, user_id, expires in reminded_rows:
        assert expires is not None
        outbox.add_event(
            session,
            Events.DOCTOR_LICENSE_EXPIRING,
            aggregate_id=doctor_id,
            data={"doctor_id": doctor_id, "user_id": user_id, "license_expires_at": expires.isoformat()},
        )
    return {"suspended": len(expired), "reminded": len(reminded_rows)}
