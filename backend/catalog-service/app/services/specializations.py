"""Mutaxassisliklar — admin (`clinics.manage`). C6 bemor qoidalari booking'da qo'llanadi."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit

from app.events import outbox
from app.models import Specialization
from app.services.errors import AlreadyExists, InvalidRequest, SpecializationNotFound


def _check(spec: Specialization) -> None:
    if (
        spec.min_patient_age is not None
        and spec.max_patient_age is not None
        and spec.min_patient_age > spec.max_patient_age
    ):
        raise InvalidRequest("min_patient_age max_patient_age dan katta bo'lmasligi kerak")


async def list_all(session: AsyncSession) -> Sequence[Specialization]:
    return (await session.scalars(select(Specialization).order_by(Specialization.name))).all()


async def create(session: AsyncSession, data: dict[str, Any], *, actor: Actor, ip: str | None) -> Specialization:
    spec = Specialization(id=uuid.uuid4(), **data)
    _check(spec)
    session.add(spec)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AlreadyExists("Bunday nom yoki slug bor") from exc
    record_audit(
        outbox,
        session,
        actor,
        action="specialization.create",
        object_type="specialization",
        object_id=spec.id,
        after={"name": spec.name},
        ip=ip,
    )
    return spec


async def update(
    session: AsyncSession, spec_id: uuid.UUID, changes: dict[str, Any], *, actor: Actor, ip: str | None
) -> Specialization:
    spec = await session.get(Specialization, spec_id, with_for_update=True)
    if spec is None:
        raise SpecializationNotFound()
    for key, value in changes.items():
        setattr(spec, key, value)
    _check(spec)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise AlreadyExists("Bunday nom yoki slug bor") from exc
    record_audit(
        outbox,
        session,
        actor,
        action="specialization.update",
        object_type="specialization",
        object_id=spec.id,
        after={k: str(v) for k, v in changes.items()},
        ip=ip,
    )
    return spec
