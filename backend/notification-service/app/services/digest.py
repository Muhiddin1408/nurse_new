"""Shifokorning kunlik digest'i (har kuni 07:00 Asia/Tashkent): "bugun N ta qabul, birinchisi HH:MM".
Faqat outbox'ga yoziladi (`digest:<doctor>:<sana>` — bir kunda bir marta, bir nechta nusxa ishlasa ham)."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import notifications as repo
from app.services.templates import LOCAL_TZ


async def enqueue_daily(session: AsyncSession, *, now: datetime | None = None) -> int:
    local_day = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ).date()
    start = datetime.combine(local_day, datetime.min.time(), tzinfo=LOCAL_TZ)
    created = 0
    for doctor_id, count, first in await repo.doctors_with_bookings(session, start, start + timedelta(days=1)):
        doctor = await repo.get_doctor(session, doctor_id)
        if doctor is None:
            continue
        created += await repo.enqueue(
            session,
            {
                "audience": "user",
                "user_id": doctor.user_id,
                "template": "doctor_daily_summary",
                "params": {"count": count, "first": first.isoformat()},
                "dedupe_key": f"digest:{doctor_id}:{local_day.isoformat()}",
            },
        )
    await session.commit()
    return created
