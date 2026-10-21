"""Shifokor to'lov rekvizitlari (D6). O'zgartirish auditga yoziladi — faqat niqoblangan raqam bilan."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction
from medbron_common.security import CurrentUser

from app.events import outbox
from app.models import PayoutAccount
from app.repositories import payouts as repo


async def save(
    session: AsyncSession, user: CurrentUser, doctor_id: uuid.UUID, values: dict[str, Any], *, ip: str | None
) -> PayoutAccount:
    async with transaction(session):
        before = await repo.account(session, doctor_id)
        before_view = before.as_dict() if before else None
        account = await repo.upsert_account(session, doctor_id, **values)
        record_audit(
            outbox,
            session,
            Actor.user(user),
            action="payout_account.update",
            object_type="payout_account",
            object_id=doctor_id,
            before=before_view,
            after=account.as_dict(),
            ip=ip,
        )
    return account
