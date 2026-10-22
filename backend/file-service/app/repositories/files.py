from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File


async def get(session: AsyncSession, file_id: uuid.UUID, *, for_update: bool = False) -> File | None:
    stmt = select(File).where(File.id == file_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.scalars(stmt)).one_or_none()


async def count_pending(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(File.owner_user_id == user_id, File.status == "pending")
    return int((await session.execute(stmt)).scalar_one())


async def stale_unattached(session: AsyncSession, before: datetime, limit: int) -> Sequence[File]:
    stmt = (
        select(File)
        .where(File.status.in_(("pending", "uploaded")), File.created_at < before)
        .order_by(File.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return (await session.scalars(stmt)).all()
