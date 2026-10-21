"""Promo kodlar (admin, `pricing.manage`). Monolitda faqat Django admin edi — endi API + audit."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.audit import Actor, record_audit
from medbron_common.db import transaction

from app.events import outbox
from app.models import PromoCode
from app.services.errors import InvalidPromoTerms, PromoCodeExists, PromoNotFound
from app.services.quotes import normalize_code

AUDITED = (
    "kind", "value", "max_discount_tiyin", "min_amount_tiyin", "valid_from", "valid_until", "max_uses",
    "max_uses_per_user", "first_booking_only", "doctor_id", "borne_by", "is_active",
)  # fmt: skip


def _check(promo: PromoCode) -> None:
    if promo.kind == "percent" and not 1 <= promo.value <= 100:
        raise InvalidPromoTerms("Foizli promo qiymati 1..100 bo'lishi kerak")
    if promo.kind == "fixed" and promo.value <= 0:
        raise InvalidPromoTerms("Qat'iy summa > 0 bo'lishi kerak")
    if promo.valid_from and promo.valid_until and promo.valid_until <= promo.valid_from:
        raise InvalidPromoTerms("valid_until valid_from dan keyin bo'lishi kerak")


def _snapshot(promo: PromoCode) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in AUDITED:
        value = getattr(promo, key)
        out[key] = (
            value.isoformat()
            if hasattr(value, "isoformat")
            else (str(value) if isinstance(value, uuid.UUID) else value)
        )
    return out


async def create(session: AsyncSession, data: dict[str, Any], *, actor: Actor, ip: str | None) -> PromoCode:
    promo = PromoCode(id=uuid.uuid4(), **{**data, "code": normalize_code(data["code"])})
    _check(promo)
    try:
        async with transaction(session):
            session.add(promo)
            await session.flush()
            record_audit(
                outbox, session, actor, action="promo.create", object_type="promo_code", object_id=promo.id,
                after={"code": promo.code, **_snapshot(promo)}, ip=ip,
            )  # fmt: skip
    except IntegrityError as exc:
        raise PromoCodeExists() from exc
    return promo


async def update(
    session: AsyncSession, promo_id: uuid.UUID, changes: dict[str, Any], *, actor: Actor, ip: str | None
) -> PromoCode:
    async with transaction(session):
        promo = await session.get(PromoCode, promo_id, with_for_update=True)
        if promo is None:
            raise PromoNotFound()
        before = _snapshot(promo)
        for key, value in changes.items():
            setattr(promo, key, value)
        _check(promo)
        after = _snapshot(promo)
        diff = {k: after[k] for k in after if after[k] != before[k]}
        if diff:
            record_audit(
                outbox, session, actor, action="promo.update", object_type="promo_code", object_id=promo.id,
                before={k: before[k] for k in diff}, after=diff, ip=ip,
            )  # fmt: skip
    return promo
