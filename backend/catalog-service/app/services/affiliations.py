"""Shifokor–klinika hamkorligi (D7, ikki tomonlama rozilik).

    invite (klinika)     → invited            → affiliation.invited
    accept (shifokor)    invited → active     → affiliation.accepted
    decline (shifokor)   invited → declined   → affiliation.ended(status=declined, ended_by=doctor)
    leave (shifokor)     active|paused → ended → affiliation.ended(status=left, ended_by=doctor)
    pause (klinika)      active → paused      → affiliation.paused   (slotlar yopiladi, bronlar SAQLANADI)
    resume (klinika)     paused → active      → affiliation.resumed
    end (klinika)        active|paused|invited → ended → affiliation.ended(status=ended, ended_by=clinic)

`ended` — booking-service kelajakdagi bronlarni 100% qaytarish bilan bekor qiladi (ARCHITECTURE 6.9);
`paused` — yo'q. Rad etilgan/tugagan qator qayta taklifda qayta ishlatiladi.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.events import Events

from app.events import outbox
from app.models import Affiliation, Clinic, Doctor
from app.repositories import catalog as repo
from app.services.clinics import require_managed
from app.services.doctors import require_doctor
from app.services.errors import AffiliationNotFound, AlreadyExists, DoctorNotFound, InvalidTransition


def _base(aff: Affiliation) -> dict[str, Any]:
    return {"affiliation_id": aff.id, "clinic_id": aff.clinic_id, "doctor_id": aff.doctor_id}


async def invite(
    session: AsyncSession,
    user_id: uuid.UUID,
    clinic_id: uuid.UUID,
    doctor_id: uuid.UUID,
    position: str,
    *,
    ip: str | None,
) -> Affiliation:
    clinic = await require_managed(session, user_id, clinic_id)
    doctor = await repo.get_doctor(session, doctor_id)
    if doctor is None or doctor.status != "approved":
        raise DoctorNotFound("Tasdiqlangan shifokor topilmadi")
    aff = await repo.affiliation_pair(session, doctor_id, clinic_id)
    if aff is not None and aff.status in ("active", "paused"):
        raise AlreadyExists("Shifokor allaqachon shu klinikada")
    if aff is not None and aff.status == "invited":
        raise AlreadyExists("Taklif allaqachon yuborilgan")
    if aff is None:
        aff = Affiliation(id=uuid.uuid4(), doctor_id=doctor_id, clinic_id=clinic_id)
        session.add(aff)
    aff.status, aff.position, aff.invited_by, aff.responded_at = "invited", position, user_id, None
    await session.flush()
    outbox.add_event(
        session,
        Events.AFFILIATION_INVITED,
        aggregate_id=aff.id,
        data={**_base(aff), "doctor_user_id": doctor.user_id, "clinic_name": clinic.name, "position": position},
    )
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "clinic_admin"),
        action="clinic.invite",
        object_type="affiliation",
        object_id=aff.id,
        after={"doctor_id": str(doctor_id)},
        ip=ip,
    )
    return aff


async def _doctor_affiliation(session: AsyncSession, user_id: uuid.UUID, affiliation_id: uuid.UUID) -> Affiliation:
    doctor = await require_doctor(session, user_id)
    aff = await repo.get_affiliation(session, affiliation_id, for_update=True)
    if aff is None or aff.doctor_id != doctor.id:
        raise AffiliationNotFound()
    return aff


async def respond(
    session: AsyncSession, user_id: uuid.UUID, affiliation_id: uuid.UUID, accept: bool, *, ip: str | None
) -> Affiliation:
    aff = await _doctor_affiliation(session, user_id, affiliation_id)
    target = "active" if accept else "declined"
    if aff.status == target:
        return aff
    if aff.status != "invited":
        raise InvalidTransition(extra={"status": aff.status})
    aff.status, aff.responded_at = target, datetime.now(UTC)
    if accept:
        outbox.add_event(session, Events.AFFILIATION_ACCEPTED, aggregate_id=aff.id, data=_base(aff))
    else:
        outbox.add_event(
            session,
            Events.AFFILIATION_ENDED,
            aggregate_id=aff.id,
            data={**_base(aff), "ended_by": "doctor", "status": "declined"},
        )
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "doctor"),
        action=f"doctor.invite.{'accept' if accept else 'decline'}",
        object_type="affiliation",
        object_id=aff.id,
        ip=ip,
    )
    return aff


async def leave(session: AsyncSession, user_id: uuid.UUID, affiliation_id: uuid.UUID, *, ip: str | None) -> Affiliation:
    aff = await _doctor_affiliation(session, user_id, affiliation_id)
    if aff.status == "ended":
        return aff
    if aff.status not in ("active", "paused"):
        raise InvalidTransition(extra={"status": aff.status})
    aff.status, aff.responded_at = "ended", datetime.now(UTC)
    outbox.add_event(
        session,
        Events.AFFILIATION_ENDED,
        aggregate_id=aff.id,
        data={**_base(aff), "ended_by": "doctor", "status": "left"},
    )
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "doctor"),
        action="doctor.affiliation.leave",
        object_type="affiliation",
        object_id=aff.id,
        ip=ip,
    )
    return aff


CLINIC_ACTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "pause": (("active",), "paused"),
    "resume": (("paused",), "active"),
    "end": (("active", "paused", "invited"), "ended"),
}


async def clinic_action(
    session: AsyncSession,
    user_id: uuid.UUID,
    clinic_id: uuid.UUID,
    affiliation_id: uuid.UUID,
    action: str,
    *,
    ip: str | None,
) -> Affiliation:
    await require_managed(session, user_id, clinic_id)
    aff = await repo.get_affiliation(session, affiliation_id, for_update=True)
    if aff is None or aff.clinic_id != clinic_id:
        raise AffiliationNotFound()
    sources, target = CLINIC_ACTIONS[action]
    if aff.status == target:
        return aff
    if aff.status not in sources:
        raise InvalidTransition(extra={"status": aff.status})
    aff.status = target
    if action == "pause":
        outbox.add_event(
            session, Events.AFFILIATION_PAUSED, aggregate_id=aff.id, data={**_base(aff), "paused_by": user_id}
        )
    elif action == "resume":
        outbox.add_event(
            session, Events.AFFILIATION_RESUMED, aggregate_id=aff.id, data={**_base(aff), "resumed_by": user_id}
        )
    else:
        outbox.add_event(
            session,
            Events.AFFILIATION_ENDED,
            aggregate_id=aff.id,
            data={**_base(aff), "ended_by": "clinic", "status": "ended"},
        )
    record_audit(
        outbox,
        session,
        Actor("user", user_id, "clinic_admin"),
        action=f"clinic.affiliation.{action}",
        object_type="affiliation",
        object_id=aff.id,
        after={"status": target},
        ip=ip,
    )
    return aff


async def doctor_affiliations(
    session: AsyncSession, user_id: uuid.UUID, statuses: tuple[str, ...]
) -> list[tuple[Affiliation, Clinic]]:
    doctor = await require_doctor(session, user_id)
    stmt = (
        select(Affiliation, Clinic)
        .join(Clinic, Clinic.id == Affiliation.clinic_id)
        .where(Affiliation.doctor_id == doctor.id, Affiliation.status.in_(statuses))
        .order_by(Affiliation.updated_at.desc())
    )
    return [(a, c) for a, c in (await session.execute(stmt)).all()]


async def clinic_doctors(
    session: AsyncSession, user_id: uuid.UUID, clinic_id: uuid.UUID
) -> list[tuple[Affiliation, Doctor]]:
    await require_managed(session, user_id, clinic_id)
    stmt = (
        select(Affiliation, Doctor)
        .join(Doctor, Doctor.id == Affiliation.doctor_id)
        .where(Affiliation.clinic_id == clinic_id, Affiliation.status != "declined")
        .order_by(Doctor.full_name)
    )
    return [(a, d) for a, d in (await session.execute(stmt)).all()]
