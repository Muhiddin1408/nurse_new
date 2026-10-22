"""Davriy ishlar (scheduler): eskirgan ochiq to'lovlar, Click fiskal cheklari, provayder jurnali retention'i.
SKIP LOCKED — bir nechta nusxa parallel ishlashi xavfsiz."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.providers import ManualRefundRequired, ProviderError
from app.core.config import get_settings
from app.models.payment import EXPIRED
from app.repositories import payments as repo
from app.services import fiscal, payloads
from app.services.deps import Deps

logger = logging.getLogger(__name__)

BATCH = 200
FISCAL_WINDOW = timedelta(days=3)


async def expire_stale(factory: async_sessionmaker[AsyncSession]) -> int:
    """`created`/`processing` holatida provayder protokoli muddatidan (12 soat) uzoq turgan urinishlar → expired.
    Bron allaqachon o'z muddati bilan yopilgan; bu faqat to'lov jadvalini toza saqlaydi (+ `payment.failed`)."""
    before = datetime.now(UTC) - timedelta(hours=get_settings().payme_transaction_timeout_hours)
    total = 0
    while True:
        async with factory() as session, session.begin():
            rows = await repo.stale_open(session, before, limit=BATCH)
            for p in rows:
                p.status = EXPIRED
                payloads.failed(session, p, reason="expired")
        total += len(rows)
        if len(rows) < BATCH:
            return total


async def submit_click_fiscal(factory: async_sessionmaker[AsyncSession], deps: Deps) -> dict[str, int]:
    """Click'da chek to'lovdan KEYIN alohida so'rov bilan (Payme — `detail` orqali, bu yerda yo'q). Yuborilmagani
    keyingi ishga tushishda qayta uriniladi (3 kun oynasi); sozlanmagan bo'lsa — jim o'tkaziladi (alert — metrika)."""
    tin = str((await deps.settings.get())["fiscal"]["tin"])
    since = datetime.now(UTC) - FISCAL_WINDOW
    ok = failed = 0
    async with factory() as session:
        todo = [
            (p.id, p.provider_state.get("click_paydoc_id"), p.amount_tiyin, p.receipt_items)
            for p in await repo.fiscal_pending(session, since, limit=BATCH)
        ]
        await session.rollback()  # tarmoq chaqiruvi paytida qulf ushlanmaydi (submit_items idempotent)
    for payment_id, paydoc_id, amount, items in todo:
        try:
            await deps.click.submit_fiscal(
                paydoc_id=paydoc_id, amount_tiyin=amount, items=fiscal.click_ofd_items(items, tin)
            )
        except (ManualRefundRequired, ProviderError) as exc:
            failed += 1
            logger.warning("click fiscal submit failed", extra={"payment_id": str(payment_id), "error": str(exc)})
            continue
        async with factory() as session, session.begin():
            locked = await repo.get(session, payment_id, for_update=True)
            if locked is not None and locked.fiscal_submitted_at is None:
                locked.fiscal_submitted_at = datetime.now(UTC)
        ok += 1
    return {"submitted": ok, "failed": failed}


async def purge_logs(factory: async_sessionmaker[AsyncSession]) -> int:
    before = datetime.now(UTC) - timedelta(days=get_settings().provider_log_retention_days)
    async with factory() as session, session.begin():
        return await repo.purge_logs(session, before)
