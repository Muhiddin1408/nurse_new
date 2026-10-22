"""Admin amallari — har biri audit bilan (biznes o'zgarishi bilan BITTA tranzaksiyada)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction
from medbron_common.security import CurrentAdmin

from app.events import outbox
from app.models import Discrepancy, Refund
from app.models.payment import ONLINE_PROVIDERS, R_FAILED, R_MANUAL, R_PENDING, REFUNDABLE_STATUSES
from app.repositories import payments as repo
from app.services import reconciliation, refunds
from app.services.errors import InvalidRegistry, PaymentNotFound, RefundNotAllowed, RefundNotFound


async def create_refund(
    session: AsyncSession,
    admin: CurrentAdmin,
    *,
    payment_id: uuid.UUID,
    amount_tiyin: int,
    reason: str,
    idempotency_key: str,
    ip: str | None,
) -> Refund:
    """Qo'lda refund (`payments.refund`). Idempotency-Key — takroriy bosishda ikkinchi refund yo'q."""
    async with transaction(session):
        existing = await repo.refund_by_source(session, "admin", idempotency_key)
        if existing is not None:
            if existing.payment_id != payment_id or existing.amount_tiyin > amount_tiyin:
                raise RefundNotAllowed("Idempotency-Key boshqa refund bilan ishlatilgan", code="IDEMPOTENCY_KEY_REUSED")
            return existing  # takroriy so'rov — o'sha refund, audit qayta yozilmaydi
        p = await repo.get(session, payment_id, for_update=True)
        if p is None:
            raise PaymentNotFound()
        if p.status not in REFUNDABLE_STATUSES:
            raise RefundNotAllowed("To'lov qaytariladigan holatda emas")
        refund = await refunds.request(
            session,
            booking_id=p.booking_id,
            amount_tiyin=amount_tiyin,
            source="admin",
            source_ref=idempotency_key,
            reason=reason or "admin",
            initiated_by="admin",
            payment_id=p.id,
        )
        if refund is None:
            raise RefundNotAllowed("Qaytariladigan qoldiq yo'q")
        if refund.payment_id != p.id:
            raise RefundNotAllowed("Idempotency-Key boshqa refund bilan ishlatilgan", code="IDEMPOTENCY_KEY_REUSED")
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="refund.request",
            object_type="refund",
            object_id=refund.id,
            after={"payment_id": str(p.id), "amount_tiyin": refund.amount_tiyin, "reason": refund.reason},
            ip=ip,
        )
    return refund


async def complete_manual(
    session: AsyncSession, admin: CurrentAdmin, refund_id: uuid.UUID, *, external_ref: str, note: str, ip: str | None
) -> Refund:
    """Kabinetda qo'lda qaytarilgan refund'ni tasdiqlash (manual_required / failed → succeeded)."""
    async with transaction(session):
        r = await repo.refund(session, refund_id, for_update=True)
        if r is None:
            raise RefundNotFound()
        if r.status not in (R_MANUAL, R_FAILED):
            raise RefundNotAllowed("Faqat qo'lda kutilayotgan yoki xato refund tasdiqlanadi")
        before = r.status
        await refunds.mark_succeeded(session, r, external_refund_id=external_ref)
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="refund.complete_manual",
            object_type="refund",
            object_id=r.id,
            before={"status": before},
            after={"status": r.status, "external_ref": external_ref, "note": note[:500]},
            ip=ip,
        )
    return r


async def retry_refund(session: AsyncSession, admin: CurrentAdmin, refund_id: uuid.UUID, *, ip: str | None) -> Refund:
    async with transaction(session):
        r = await repo.refund(session, refund_id, for_update=True)
        if r is None:
            raise RefundNotFound()
        if r.status not in (R_MANUAL, R_FAILED):
            raise RefundNotAllowed("Faqat xato yoki qo'lda kutilayotgan refund qayta navbatga qo'yiladi")
        before = r.status
        r.status, r.attempts, r.next_attempt_at = R_PENDING, 0, datetime.now(UTC)
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="refund.retry",
            object_type="refund",
            object_id=r.id,
            before={"status": before},
            after={"status": R_PENDING},
            ip=ip,
        )
    return r


async def import_registry(
    session: AsyncSession, admin: CurrentAdmin, *, provider: str, day: date, csv_text: str, ip: str | None
) -> list[Discrepancy]:
    if provider not in ONLINE_PROVIDERS:
        raise InvalidRegistry("provider: payme | click")
    txs = reconciliation.parse_registry(csv_text)
    async with transaction(session):
        found = await reconciliation.reconcile_provider(session, provider, day, txs)
        record_audit(
            outbox,
            session,
            Actor.admin(admin),
            action="reconciliation.import",
            object_type="reconciliation",
            object_id=f"{provider}:{day.isoformat()}",
            after={"rows": len(txs), "discrepancies": len(found)},
            ip=ip,
        )
    return found


async def resolve_discrepancy(
    session: AsyncSession, admin: CurrentAdmin, discrepancy_id: uuid.UUID, *, note: str, ip: str | None
) -> Discrepancy:
    async with transaction(session):
        d = await repo.discrepancy(session, discrepancy_id, for_update=True)
        if d is None:
            raise RefundNotFound("Farq topilmadi", code="DISCREPANCY_NOT_FOUND")
        if d.status != "resolved":
            d.status, d.resolution_note, d.resolved_by = "resolved", note[:2000], admin.admin_id
            record_audit(
                outbox,
                session,
                Actor.admin(admin),
                action="reconciliation.resolve",
                object_type="discrepancy",
                object_id=d.id,
                before={"status": "open"},
                after={"status": "resolved", "note": note[:500]},
                ip=ip,
            )
    return d
