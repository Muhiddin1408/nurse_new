"""Kunlik solishtirish (B7). Maqsad: farqlar soni doimiy 0; har farq → `payment.discrepancy_found` (ops alert)
+ admin navbati (`/v1/admin/reconciliation`).

1. TASHQI: provayder reestri (kabinetdan CSV) ↔ bizning to'lovlar:
       provider_only    🔴 pul olindi, bizda yo'q — ENG XAVFLI
       local_only       🔴 bizda yechilgan, provayderda yo'q
       amount_mismatch  🔴
       status_mismatch  🟠
2. ICHKI (har kecha, provayderga bog'liq emas):
       missing_ledger    — pul yechilgan to'lov daftarga tushmagan
       unresolved_refund — needs_refund, lekin Refund yaratilmagan
       ledger_imbalance  — sum(debit) ≠ sum(credit)

"Tasdiqlangan bron, to'lov yo'q" tekshiruvi monolitda shu yerda edi — endi bron ma'lumoti booking'da;
booking `payment_id` siz tasdiqlangan bronni o'zi ko'radi (`confirmed` faqat `payment.captured` dan yoki at_clinic).
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.eventbus.envelope import new_event_id

from app.metrics import DISCREPANCIES
from app.models import Discrepancy, Payment
from app.models.payment import (
    CANCELLED,
    EXPIRED,
    MONEY_TAKEN_STATUSES,
    PARTIALLY_REFUNDED,
    PROCESSING,
    REFUNDED,
    REVERSED,
    SUCCEEDED,
)
from app.repositories import payments as repo
from app.services import payloads
from app.services.errors import InvalidRegistry

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Asia/Tashkent")
MAX_REGISTRY_ROWS = 100_000

# Reestr holati (Payme kodlari; Click reestri ham shu formatga keltiriladi) → bizning holatlar
STATE_TO_STATUSES: dict[int, set[str]] = {
    1: {PROCESSING},
    2: {SUCCEEDED, PARTIALLY_REFUNDED, REFUNDED},
    -1: {CANCELLED, EXPIRED},
    -2: {REVERSED, REFUNDED},
}
MONEY_TAKEN_STATES = {2, -2}


@dataclass(frozen=True)
class ProviderTx:
    external_id: str
    amount_tiyin: int
    state: int
    booking_id: str


def day_window_ms(day: date) -> tuple[int, int]:
    start = datetime.combine(day, time.min, tzinfo=LOCAL_TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000) - 1


def parse_registry(text: str) -> list[ProviderTx]:
    """Kabinet reestri (CSV, UTF-8, sarlavha bilan): `id, amount (tiyin), state, booking_id` (qolgan ustunlar
    e'tiborsiz). ⚠️ Haqiqiy format Payme/Click bilan tasdiqlanib, kerak bo'lsa shu parser moslanadi."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or not {"id", "amount", "state"} <= set(reader.fieldnames):
        raise InvalidRegistry("Reestr ustunlari: id, amount, state[, booking_id]")
    rows: list[ProviderTx] = []
    for line, r in enumerate(reader, start=2):
        if len(rows) >= MAX_REGISTRY_ROWS:
            raise InvalidRegistry(f"Reestr juda katta (> {MAX_REGISTRY_ROWS} qator)")
        try:
            rows.append(
                ProviderTx(
                    external_id=r["id"].strip(),
                    amount_tiyin=int(r["amount"]),
                    state=int(r["state"]),
                    booking_id=(r.get("booking_id") or "").strip(),
                )
            )
        except (TypeError, ValueError):
            raise InvalidRegistry(f"{line}-qator noto'g'ri") from None
    return rows


async def _record(session: AsyncSession, day: date, type_: str, **fields: Any) -> Discrepancy | None:
    p: Payment | None = fields.pop("payment", None)
    if p is not None:
        fields.setdefault("payment_id", p.id)
        fields.setdefault("booking_id", p.booking_id)
        fields.setdefault("provider", p.provider)
        fields.setdefault("external_id", p.external_id)
    fingerprint = ":".join(str(fields.get(k) or "") for k in ("provider", "external_id", "payment_id", "booking_id"))
    fingerprint = f"{type_}:{fingerprint}"
    if type_ != "ledger_imbalance" and await repo.resolved_fingerprint_exists(session, fingerprint):
        return None  # admin allaqachon ko'rib chiqib hal qilgan
    if await repo.discrepancy_exists(session, fingerprint, day):
        return None
    d = Discrepancy(id=new_event_id(), type=type_, day=day, fingerprint=fingerprint, status="open", **fields)
    try:
        async with session.begin_nested():
            session.add(d)
            await session.flush()
    except IntegrityError:
        return None  # shu kun uchun parallel yozildi
    payloads.discrepancy_found(session, d)
    DISCREPANCIES.labels(type=type_).inc()
    logger.error("ALERT: payment discrepancy", extra={"type": type_, "fingerprint": fingerprint})
    return d


async def reconcile_provider(
    session: AsyncSession, provider: str, day: date, txs: list[ProviderTx]
) -> list[Discrepancy]:
    frm, to = day_window_ms(day)
    local = {p.external_id: p for p in await repo.in_window(session, provider, frm, to) if p.external_id}
    found: list[Discrepancy | None] = []
    seen: set[str] = set()
    for tx in txs:
        seen.add(tx.external_id)
        p = local.get(tx.external_id) or await repo.by_external(session, provider, tx.external_id)
        if p is None:
            if tx.state in MONEY_TAKEN_STATES:
                found.append(
                    await _record(
                        session,
                        day,
                        "provider_only",
                        provider=provider,
                        external_id=tx.external_id,
                        provider_tiyin=tx.amount_tiyin,
                        details={"state": tx.state, "account_booking_id": tx.booking_id},
                    )
                )
            continue
        if p.amount_tiyin != tx.amount_tiyin:
            found.append(
                await _record(
                    session,
                    day,
                    "amount_mismatch",
                    payment=p,
                    local_tiyin=p.amount_tiyin,
                    provider_tiyin=tx.amount_tiyin,
                )
            )
        if p.status not in STATE_TO_STATUSES.get(tx.state, set()):
            found.append(
                await _record(
                    session,
                    day,
                    "status_mismatch",
                    payment=p,
                    details={"local_status": p.status, "provider_state": tx.state},
                )
            )
    for external_id, p in local.items():
        if external_id not in seen and p.status in MONEY_TAKEN_STATUSES:
            found.append(await _record(session, day, "local_only", payment=p, local_tiyin=p.amount_tiyin))
    return [d for d in found if d]


async def reconcile_internal(session: AsyncSession, day: date) -> list[Discrepancy]:
    found: list[Discrepancy | None] = []
    for p in await repo.money_taken_without_capture(session):
        found.append(await _record(session, day, "missing_ledger", payment=p, local_tiyin=p.amount_tiyin))
    for p in await repo.needs_refund_without_refund(session):
        found.append(await _record(session, day, "unresolved_refund", payment=p, local_tiyin=p.amount_tiyin))
    imbalance = await repo.trial_balance(session)
    if imbalance != 0:
        found.append(await _record(session, day, "ledger_imbalance", details={"imbalance_tiyin": imbalance}))
    return [d for d in found if d]


def yesterday() -> date:
    return datetime.now(LOCAL_TZ).date() - timedelta(days=1)
