"""Quote (narx hisobi) va commit (promo/paket band qilish) — internal API va ommaviy oldindan ko'rish.

Quote Redis'da `QUOTE_TTL_SECONDS` (10 daq): booking hold'dan keyin commit qiladi, narx qayta hisoblanmaydi.
Commit `booking_id` bo'yicha idempotent; band qilish `reserved` — `booking.created` kelsa `active`,
kelmasa sweeper 15 daqiqada bo'shatadi (ADR-0011).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.db import transaction

from app.clients.admin import AdminClient
from app.clients.catalog import CatalogClient, DoctorNotFound
from app.core.config import get_settings
from app.models import RELEASED, RESERVED, PackageUse, PromoCode, PromoRedemption, ServiceCache
from app.repositories import pricing as repo
from app.services import calc
from app.services.errors import (
    DoctorNotBookable,
    PackageExhausted,
    PromoInvalidError,
    PromoLimitReached,
    QuoteExpired,
    ServicesUnavailable,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deps:
    catalog: CatalogClient
    admin: AdminClient
    redis: Redis


@dataclass(frozen=True)
class QuoteRequest:
    client_id: uuid.UUID
    doctor_id: uuid.UUID
    service_ids: list[uuid.UUID]
    slot_start_at: datetime | None
    price_adjust_percent: int
    place: str | None
    promo_code: str = ""


def normalize_code(code: str) -> str:
    return code.strip().upper()


def _quote_key(quote_id: uuid.UUID | str) -> str:
    return f"quote:{quote_id}"


def _service_values(item: dict[str, Any], changed_at: datetime) -> dict[str, Any]:
    return {
        "service_id": uuid.UUID(item["id"]),
        "doctor_id": uuid.UUID(item["doctor_id"]) if item.get("doctor_id") else None,
        "clinic_id": uuid.UUID(item["clinic_id"]) if item.get("clinic_id") else None,
        "specialization_id": uuid.UUID(item["specialization_id"]),
        "name": item["name"],
        "name_ru": item.get("name_ru") or "",
        "place": item["place"],
        "duration_minutes": int(item["duration_minutes"]),
        "price_tiyin": int(item["price_tiyin"]),
        "is_active": bool(item["is_active"]),
        "mxik_code": item.get("mxik_code") or "",
        "package_code": item.get("package_code") or "",
        "vat_percent": item.get("vat_percent"),
        "changed_at": changed_at,
    }


async def load_services(
    session: AsyncSession, deps: Deps, ids: Sequence[uuid.UUID], doctor_id: uuid.UUID
) -> dict[uuid.UUID, ServiceCache]:
    """Proyeksiyadan; yo'q bo'lganlari — catalog'dan (`bookable_for`) o'qib keshga yoziladi."""
    found = await repo.services_by_ids(session, ids)
    missing = [i for i in ids if i not in found]
    if missing:
        items = await deps.catalog.services(missing, bookable_for=doctor_id)
        # Catalog javobi eventdan eskiroq bo'lmasligi uchun — epoch emas, "hozir" emas: proyeksiya keyingi
        # eventi (yangiroq occurred_at) baribir ustidan yozadi.
        changed_at = datetime(2000, 1, 1, tzinfo=UTC)
        for item in items:
            await repo.upsert_service(session, _service_values(item, changed_at), keep_fiscal=False)
        await session.flush()
        found.update(await repo.services_by_ids(session, missing))
    return found


def _validate_services(services: dict[uuid.UUID, ServiceCache], req: QuoteRequest) -> tuple[list[ServiceCache], str]:
    if len(set(req.service_ids)) != len(req.service_ids):
        raise ServicesUnavailable("Xizmatlar takrorlangan")
    rows = [services.get(i) for i in req.service_ids]
    if any(r is None or not r.is_active for r in rows):
        raise ServicesUnavailable()
    valid = [r for r in rows if r is not None]
    # Shifokorning o'z xizmati yoki klinika xizmati (klinika–shifokor aloqasini booking catalog'da tekshiradi)
    if any(r.doctor_id is not None and r.doctor_id != req.doctor_id for r in valid):
        raise ServicesUnavailable()
    places = {r.place for r in valid}
    if len(places) != 1 or (req.place is not None and req.place not in places):
        raise ServicesUnavailable("Klinika va uy chaqiruvi xizmatlarini aralashtirib bo'lmaydi")
    return valid, places.pop()


async def compute(
    session: AsyncSession, deps: Deps, req: QuoteRequest, *, now: datetime | None = None
) -> dict[str, Any]:
    """Quote (`contracts/openapi/pricing-service.internal.yaml` `Quote`) + ichki maydonlar (`_reserve`)."""
    now = now or datetime.now(UTC)
    settings = get_settings()
    platform = await deps.admin.settings.get()
    try:
        doctor = await deps.catalog.doctor_settings(req.doctor_id)
    except DoctorNotFound as exc:
        raise DoctorNotBookable() from exc
    if not doctor.bookable:
        raise DoctorNotBookable()
    services = await load_services(session, deps, req.service_ids, req.doctor_id)
    rows, place = _validate_services(services, req)

    pct = calc.clamp_adjust(req.price_adjust_percent)
    base = {r.service_id: r.price_tiyin for r in rows}
    prices = calc.adjusted_prices(base, pct)
    original = sum(prices.values())

    has_previous = await repo.has_previous_booking(session, req.client_id)
    promo: calc.Discount | None = None
    code = normalize_code(req.promo_code)
    if code:
        row = await repo.promo_by_code(session, code)
        terms = None
        usage = calc.PromoUsage(0, 0)
        if row is not None:
            terms = calc.PromoTerms(
                id=row.id, code=row.code, kind=row.kind, value=row.value, max_discount_tiyin=row.max_discount_tiyin,
                min_amount_tiyin=row.min_amount_tiyin, valid_from=row.valid_from, valid_until=row.valid_until,
                max_uses=row.max_uses, max_uses_per_user=row.max_uses_per_user,
                first_booking_only=row.first_booking_only, doctor_id=row.doctor_id, borne_by=row.borne_by,
            )  # fmt: skip
            usage = calc.PromoUsage(*await repo.promo_usage(session, row.id, req.client_id))
        try:
            promo = calc.promo_discount(
                terms,
                original_tiyin=original,
                doctor_id=req.doctor_id,
                has_previous_booking=has_previous,
                usage=usage,
                now=now,
            )
        except calc.PromoInvalid as exc:
            # Monolit bilan bir xil: noto'g'ri promo bronni to'xtatadi (boshqa chegirma yutsa ham)
            raise PromoInvalidError(exc.message, extra={"reason": exc.reason}) from exc

    await repo.expire_stale_enrollments(session, req.client_id, now)
    offers = []
    for enrollment in await repo.active_enrollments(
        session, req.client_id, now=now, doctor_id=req.doctor_id, service_ids=list(prices)
    ):
        used = await repo.sessions_used(session, enrollment.id)
        offers.append(
            calc.EnrollmentOffer(
                enrollment.id, enrollment.service_id, enrollment.discount_percent, enrollment.sessions_total - used
            )
        )
    discount = calc.best_discount(
        calc.DiscountInputs(
            original_tiyin=original,
            prices=prices,
            first_booking_percent=int(platform["pricing"]["first_booking_discount_percent"]),
            has_previous_booking=has_previous,
            follow_up_percent=doctor.follow_up_discount_percent,
            has_recent_completed_with_doctor=await repo.has_recent_completed(
                session, req.client_id, req.doctor_id, now - timedelta(days=settings.follow_up_window_days)
            ),
            package_offers=offers,
            promo=promo,
        )
    )
    commission_bp = int(platform["commission"]["platform_bp"])
    provider_bp = int(platform["commission"]["payme_fee_bp"])  # monolit: bron paytida doim Payme stavkasi
    split = calc.split_with_discount(original, discount, commission_bp, provider_bp)
    plan = calc.resolve_payment_mode(
        total_tiyin=split.total_tiyin,
        is_home_visit=place == "home",
        doctor_mode=doctor.clinic_payment_mode,
        client_no_shows=await repo.client_no_shows(session, req.client_id),
        deposit_percent=int(platform["payment"]["deposit_percent"]),
        force_prepaid_after_no_shows=int(platform["payment"]["force_prepaid_after_no_shows"]),
    )
    fiscal = platform["fiscal"]
    items = [
        {
            "service_id": str(r.service_id),
            "service_name": r.name,
            "base_price_tiyin": r.price_tiyin,
            "price_tiyin": prices[r.service_id],
            "duration_minutes": r.duration_minutes,
            "mxik_code": r.mxik_code or fiscal["default_mxik_code"],
            "package_code": r.package_code or fiscal["default_package_code"],
            "vat_percent": r.vat_percent if r.vat_percent is not None else int(fiscal["default_vat_percent"]),
        }
        for r in rows
    ]
    quote_id = uuid.uuid4()
    return {
        "quote_id": str(quote_id),
        "expires_at": (now + timedelta(seconds=settings.quote_ttl_seconds)).isoformat(),
        "items": items,
        "original_tiyin": original,
        "price_adjust_percent": pct,
        "discount_kind": discount.kind,
        "discount_tiyin": discount.amount_tiyin,
        "promo_code": discount.promo_code if discount.kind == calc.PROMO else "",
        "total_tiyin": split.total_tiyin,
        "money": {
            "total_tiyin": split.total_tiyin,
            "platform_fee_tiyin": split.platform_fee_tiyin,
            "provider_fee_tiyin": split.provider_fee_tiyin,
            "doctor_payout_tiyin": split.doctor_payout_tiyin,
            "discount_tiyin": discount.amount_tiyin,
            "discount_borne_by": discount.borne_by,
        },
        "payment_mode": plan.mode,
        "prepay_tiyin": plan.prepay_tiyin,
        "commission_bp": commission_bp,
        "provider_fee_bp": provider_bp,
        "_reserve": {
            "client_id": str(req.client_id),
            "promo_id": str(discount.promo_id) if discount.promo_id else None,
            "enrollment_id": str(discount.enrollment_id) if discount.enrollment_id else None,
            "amount_tiyin": discount.amount_tiyin,
        },
    }


def public(quote: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in quote.items() if not k.startswith("_")}


async def create(session: AsyncSession, deps: Deps, req: QuoteRequest) -> dict[str, Any]:
    async with transaction(session):
        quote = await compute(session, deps, req)
    await deps.redis.set(_quote_key(quote["quote_id"]), json.dumps(quote), ex=get_settings().quote_ttl_seconds)
    return public(quote)


async def preview(session: AsyncSession, deps: Deps, req: QuoteRequest) -> dict[str, Any]:
    """Ommaviy oldindan ko'rish: hech narsa saqlanmaydi va band qilinmaydi."""
    async with transaction(session):
        return public(await compute(session, deps, req))


async def commit(
    session: AsyncSession, deps: Deps, quote_id: uuid.UUID, *, booking_id: uuid.UUID, client_id: uuid.UUID
) -> dict[str, Any]:
    raw = await deps.redis.get(_quote_key(quote_id))
    if raw is None:
        raise QuoteExpired()
    quote: dict[str, Any] = json.loads(raw)
    reserve = quote["_reserve"]
    if reserve["client_id"] != str(client_id):
        raise QuoteExpired()  # begona quote — xuddi yo'qdek
    try:
        async with transaction(session):
            if await repo.redemption_for_booking(session, booking_id) or await repo.use_for_booking(
                session, booking_id
            ):
                return public(quote)  # takroriy commit
            if reserve["promo_id"]:
                await _reserve_promo(
                    session, uuid.UUID(reserve["promo_id"]), booking_id, client_id, reserve["amount_tiyin"]
                )
            elif reserve["enrollment_id"]:
                await _reserve_package(
                    session, uuid.UUID(reserve["enrollment_id"]), booking_id, reserve["amount_tiyin"]
                )
    except IntegrityError:
        # Parallel ikki commit bir booking_id bilan — ikkinchisi birinchining natijasini oladi
        logger.info("concurrent commit", extra={"booking_id": str(booking_id)})
    return public(quote)


async def _reserve_promo(
    session: AsyncSession, promo_id: uuid.UUID, booking_id: uuid.UUID, client_id: uuid.UUID, amount: int
) -> None:
    promo = await session.get(PromoCode, promo_id, with_for_update=True)  # limit tekshiruvi ketma-ket
    if promo is None or not promo.is_active:
        raise PromoLimitReached("Promo kod endi faol emas")
    total, mine = await repo.promo_usage(session, promo_id, client_id)
    if (promo.max_uses is not None and total >= promo.max_uses) or mine >= promo.max_uses_per_user:
        raise PromoLimitReached()
    session.add(
        PromoRedemption(
            id=uuid.uuid4(), promo_id=promo_id, booking_id=booking_id, client_id=client_id,
            amount_tiyin=amount, state=RESERVED,
        )
    )  # fmt: skip
    await session.flush()


async def _reserve_package(session: AsyncSession, enrollment_id: uuid.UUID, booking_id: uuid.UUID, amount: int) -> None:
    now = datetime.now(UTC)
    enrollment = await repo.get_enrollment(session, enrollment_id, for_update=True)
    if enrollment is None or not enrollment.is_active or enrollment.expires_at <= now:
        raise PackageExhausted()
    used = await repo.sessions_used(session, enrollment.id)
    if used >= enrollment.sessions_total:
        raise PackageExhausted()
    session.add(
        PackageUse(
            id=uuid.uuid4(), enrollment_id=enrollment.id, booking_id=booking_id, amount_tiyin=amount, state=RESERVED
        )
    )
    if used + 1 >= enrollment.sessions_total:
        enrollment.is_active = False  # seanslar tugadi — keyin qayta yozilish mumkin
    await session.flush()


async def release(session: AsyncSession, booking_id: uuid.UUID) -> None:
    """Bron bekor / muddati o'tdi: promo limiti va paket seansi qaytadi (consumer tranzaksiyasi ichida)."""
    use = await repo.release_for_booking(session, booking_id)
    if use is not None:
        await reactivate_if_needed(session, use.enrollment_id)


async def reactivate_if_needed(session: AsyncSession, enrollment_id: uuid.UUID) -> None:
    """ADR-0011 #5: monolitda seanslar tugab yopilgan yozilishga qaytgan seans ishlatib bo'lmasdi."""
    enrollment = await repo.get_enrollment(session, enrollment_id, for_update=True)
    if enrollment is None or enrollment.is_active or enrollment.expires_at <= datetime.now(UTC):
        return
    if await repo.sessions_used(session, enrollment.id) >= enrollment.sessions_total:
        return
    if await repo.has_other_active_enrollment(session, enrollment):
        return  # mijoz yangi paketga yozilib bo'lgan — UNIQUE (client, service) faol
    enrollment.is_active = True


async def sweep_stale_reservations(session: AsyncSession, *, limit: int = 500) -> int:
    """`booking.created` kelmagan (bron yaratilmay qolgan) band qilishlar — bo'shatiladi."""
    before = datetime.now(UTC) - timedelta(minutes=get_settings().reservation_ttl_minutes)
    async with transaction(session):
        promos, uses = await repo.stale_reservations(session, before, limit)
        for p in promos:
            p.state = RELEASED
        for u in uses:
            u.state = RELEASED
        await session.flush()
        for u in uses:
            await reactivate_if_needed(session, u.enrollment_id)
    return len(promos) + len(uses)
