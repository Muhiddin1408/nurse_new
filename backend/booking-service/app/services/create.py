"""Bron yaratish sagasi (ADR-0011). Qadamlar tartibi ataylab:

1. Idempotency kaliti — ALOHIDA tranzaksiyada birinchi (parallel ikki so'rovdan bittasi to'xtaydi).
2. O'qishlar (yon ta'sirsiz): catalog, user, schedule (slot), sozlamalar → tekshiruvlar.
3. pricing quote — hold'dan OLDIN (noto'g'ri promo slotni band qilmaydi).
4. schedule hold → pricing commit → (to'lovsiz bo'lsa schedule confirm) → bron qatori + eventlar (bitta tx).
5. Xatoda kompensatsiya: hold `release` (yetmasa — TTL bilan o'zi bo'shaydi), pricing band qilishi
   `booking.created` kelmasa 15 daqiqada o'zi bo'shaydi, idempotency qatori o'chiriladi (qayta urinish mumkin).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medbron_common.db import transaction
from medbron_common.eventbus.envelope import new_event_id

from app.clients.upstream import Slot, UpstreamRejected
from app.core.config import get_settings
from app.metrics import BOOKINGS_CREATED, SLOT_CONFLICTS
from app.models import Booking, BookingItem
from app.models.booking import CONFIRMED, PENDING_PAYMENT
from app.repositories import bookings as repo
from app.services import lifecycle, payloads, policy
from app.services.deps import Deps
from app.services.errors import (
    DoctorNotBookable,
    IdempotencyInProgress,
    IdempotencyMismatch,
    InvalidBookingRequest,
    PromoInvalid,
    SlotTaken,
    TooManyPending,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateRequest:
    client_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    slot_id: uuid.UUID
    service_ids: list[uuid.UUID]
    address_id: uuid.UUID | None
    comment: str
    promo_code: str
    idempotency_key: str | None

    def canonical(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "doctor_id": self.doctor_id,
            "slot_id": self.slot_id,
            "service_ids": sorted(map(str, self.service_ids)),
            "address_id": self.address_id,
            "comment": self.comment,
            "promo_code": self.promo_code.strip().upper(),
        }


@dataclass(frozen=True)
class CreateResult:
    booking: Booking
    replayed: bool


async def _alternatives(deps: Deps, slot: Slot | None, doctor_id: uuid.UUID, minutes: int) -> list[dict[str, Any]]:
    """409 SLOT_TAKEN da ≤ 3 ta muqobil vaqt (monolit: shu joy turi, slotdan bir kun oldindan)."""
    try:
        start = max(datetime.now(UTC), (slot.start_at if slot else datetime.now(UTC)) - timedelta(days=1))
        found = await deps.schedule.free_slots(
            doctor_id,
            start=start,
            end=start + timedelta(days=7),
            place=slot.place if slot else None,
            min_minutes=minutes,
            limit=3,
        )
    except Exception:
        return []
    return [{"slot_id": str(s.id), "start_at": s.start_at.isoformat()} for s in found]


async def _replay(session: AsyncSession, req: CreateRequest, digest: str) -> Booking | None:
    """Kalit band qilinadi. Qaytaradi: oldingi bron (takroriy so'rov) yoki None (yangi saga)."""
    if not req.idempotency_key:
        return None
    async with transaction(session):
        existing = await repo.claim_key(session, req.client_id, req.idempotency_key, digest)
    if existing is None:
        return None
    if existing.request_hash != digest:
        raise IdempotencyMismatch()
    if existing.booking_id is None:
        raise IdempotencyInProgress()
    booking = await repo.get(session, existing.booking_id)
    if booking is None:  # pragma: no cover — bron o'chirilmaydi
        raise IdempotencyInProgress()
    return booking


async def create_booking(session: AsyncSession, deps: Deps, req: CreateRequest) -> CreateResult:
    digest = policy.request_hash(req.canonical())
    replay = await _replay(session, req, digest)
    if replay is not None:
        return CreateResult(replay, replayed=True)
    try:
        booking = await _saga(session, deps, req)
    except BaseException:
        if req.idempotency_key:
            async with transaction(session):
                await repo.drop_key(session, req.client_id, req.idempotency_key)
        raise
    BOOKINGS_CREATED.labels(status=booking.status, place=booking.place).inc()
    return CreateResult(booking, replayed=False)


Loaded = tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    dict[str, Any] | None,
    Slot | None,
    dict[str, Any] | None,
    dict[str, Any],
]


async def _load(deps: Deps, req: CreateRequest) -> Loaded:
    """Mustaqil o'qishlar parallel — bron yaratish kechikishi eng sekin chaqiruvga teng."""

    async def address() -> dict[str, Any] | None:
        return await deps.users.address(req.client_id, req.address_id) if req.address_id else None

    doctor, services, patient, slot, addr, platform = await asyncio.gather(
        deps.catalog.bookability(req.doctor_id),
        deps.catalog.bookable_services(req.doctor_id, req.service_ids),
        deps.users.patient(req.client_id, req.patient_id),
        deps.schedule.slot(req.slot_id),
        address(),
        deps.settings.get(),
    )
    return doctor, services, patient, slot, addr, platform


async def _saga(session: AsyncSession, deps: Deps, req: CreateRequest) -> Booking:
    settings = get_settings()
    if not req.service_ids or len(set(req.service_ids)) != len(req.service_ids):
        raise InvalidBookingRequest("Xizmatlar ro'yxati bo'sh yoki takrorlangan")
    doctor, services, patient, slot, address, platform = await _load(deps, req)
    now = datetime.now(UTC)

    if doctor is None or not doctor["bookable"]:
        raise DoctorNotBookable()
    if patient is None:
        raise InvalidBookingRequest("patient_id noto'g'ri")
    # A2: begona va yo'q slot — bir xil javob
    if slot is None or slot.doctor_id != req.doctor_id:
        raise InvalidBookingRequest("slot_id noto'g'ri")
    if len(services) != len(req.service_ids):
        raise InvalidBookingRequest("Xizmat topilmadi yoki bu shifokor uchun mavjud emas")
    places = {s["place"] for s in services}
    if len(places) != 1:
        raise InvalidBookingRequest("Uy chaqiruvi va klinika xizmatlarini bitta bronda aralashtirib bo'lmaydi")
    place = places.pop()
    if place != slot.place:
        raise InvalidBookingRequest("Xizmat joyi tanlangan vaqt bilan mos emas")
    for s in services:
        if s["doctor_id"] is None and str(s["clinic_id"]) != str(slot.clinic_id):
            raise InvalidBookingRequest("Klinika xizmati boshqa klinikadagi vaqt uchun tanlangan")
    # ADR-0011 #2: monolitda yaratishda lead time tekshirilmasdi
    lead = timedelta(minutes=int(platform["schedule"]["min_lead_time_minutes"]))
    if slot.start_at < now + lead:
        raise InvalidBookingRequest("Bu vaqt juda yaqin yoki o'tib ketgan — boshqa vaqtni tanlang")
    total_minutes = sum(int(s["duration_minutes"]) for s in services)
    if total_minutes > slot.duration_minutes:
        raise InvalidBookingRequest("Tanlangan xizmatlar bu vaqtga sig'maydi")

    buffer: int | None = None
    if place == "home":
        if address is None:
            raise InvalidBookingRequest("Uy chaqiruvi uchun manzil (address_id) majburiy")
        km = policy.distance_km(doctor.get("home_base"), address)
        if km is not None and km > float(doctor["home_visit_radius_km"]):
            raise InvalidBookingRequest("Shifokor bu manzilga bormaydi")
        buffer = policy.travel_buffer_minutes(km)
    elif req.address_id and address is None:
        raise InvalidBookingRequest("address_id noto'g'ri")

    appointment_day = slot.start_at.astimezone(policy.LOCAL_TZ).date()
    birth = date.fromisoformat(patient["birth_date"])
    for s in services:
        problem = policy.patient_fit_error(s["patient_rules"], birth, patient["gender"], appointment_day)
        if problem:
            raise InvalidBookingRequest(problem)

    if await repo.live_pending_count(session, req.client_id, now) >= settings.max_pending_bookings_per_client:
        raise TooManyPending()
    if await repo.patient_overlap(session, req.patient_id, slot.start_at, slot.end_at, now):
        raise InvalidBookingRequest(
            "Bemorning shu vaqtga to'g'ri keladigan boshqa broni bor", code="PATIENT_BUSY", status_code=409
        )

    try:
        quote = await deps.pricing.quote(
            {
                "client_id": str(req.client_id),
                "doctor_id": str(req.doctor_id),
                "service_ids": [str(s) for s in req.service_ids],
                "slot_start_at": slot.start_at.isoformat(),
                "price_adjust_percent": slot.price_adjust_percent,
                "place": place,
                "promo_code": req.promo_code,
            }
        )
    except UpstreamRejected as exc:
        if exc.code == "PROMO_INVALID":
            raise PromoInvalid(
                exc.detail.get("message") or "Promo kod yaroqsiz", extra={"reason": exc.detail.get("reason")}
            ) from exc
        raise InvalidBookingRequest("Xizmat narxini hisoblab bo'lmadi") from exc

    booking_id = new_event_id()  # UUIDv7 — vaqt bo'yicha tartiblanadi
    ttl = int(platform["schedule"]["hold_ttl_minutes"]) * 60
    try:
        held = await deps.schedule.hold(
            req.slot_id, hold_ref=booking_id, doctor_id=req.doctor_id, ttl_seconds=ttl, travel_buffer_minutes=buffer
        )
    except UpstreamRejected as exc:
        SLOT_CONFLICTS.inc()
        raise SlotTaken(
            extra={
                "alternatives": await _alternatives(deps, slot, req.doctor_id, total_minutes),
                "waitlist_available": True,
            }
        ) from exc

    try:
        try:
            quote = await deps.pricing.commit(quote["quote_id"], booking_id=booking_id, client_id=req.client_id)
        except UpstreamRejected as exc:
            raise PromoInvalid(
                "Promo kod yoki paket boshqa bronda ishlatildi — qayta urinib ko'ring", extra={"reason": exc.code}
            ) from exc
        status = PENDING_PAYMENT if int(quote["prepay_tiyin"]) > 0 else CONFIRMED
        if status == CONFIRMED:
            held = await deps.schedule.confirm(req.slot_id, booking_id)
        return await _persist(session, req, booking_id, held, place, quote, patient, address, status, now)
    except BaseException:
        try:
            await deps.schedule.release(req.slot_id, booking_id)
        except Exception:
            logger.warning("compensation: slot release failed", extra={"slot_id": str(req.slot_id)})
        raise


async def _persist(
    session: AsyncSession,
    req: CreateRequest,
    booking_id: uuid.UUID,
    slot: Slot,
    place: str,
    quote: dict[str, Any],
    patient: dict[str, Any],
    address: dict[str, Any] | None,
    status: str,
    now: datetime,
) -> Booking:
    money = quote["money"]
    for _ in range(5):
        number = policy.booking_number(now)
        if not await repo.number_exists(session, number):
            break
    booking = Booking(
        id=booking_id,
        number=number,
        client_id=req.client_id,
        patient_id=req.patient_id,
        doctor_id=req.doctor_id,
        clinic_id=slot.clinic_id,
        slot_id=req.slot_id,
        address_id=req.address_id if place == "home" else None,
        place=place,
        start_at=slot.start_at,
        end_at=slot.end_at,
        status=status,
        quote_id=uuid.UUID(quote["quote_id"]),
        payment_mode=quote["payment_mode"],
        payment_due_at=slot.hold_expires_at if status == PENDING_PAYMENT else None,
        original_tiyin=int(quote["original_tiyin"]),
        price_adjust_percent=int(quote["price_adjust_percent"]),
        discount_kind=quote["discount_kind"],
        discount_tiyin=int(quote["discount_tiyin"]),
        discount_borne_by=money["discount_borne_by"],
        promo_code=quote["promo_code"],
        total_tiyin=int(quote["total_tiyin"]),
        prepay_tiyin=int(quote["prepay_tiyin"]),
        commission_bp=int(quote["commission_bp"]),
        provider_fee_bp=int(quote["provider_fee_bp"]),
        platform_fee_tiyin=int(money["platform_fee_tiyin"]),
        provider_fee_tiyin=int(money["provider_fee_tiyin"]),
        doctor_payout_tiyin=int(money["doctor_payout_tiyin"]),
        patient={k: patient[k] for k in ("full_name", "relation", "birth_date", "gender", "weight_kg")},
        address=_address_snapshot(address) if place == "home" and address else None,
        client_comment=req.comment,
        confirmed_at=now if status == CONFIRMED else None,
    )
    items = [
        BookingItem(
            booking_id=booking_id,
            service_id=uuid.UUID(i["service_id"]),
            service_name=i["service_name"],
            base_price_tiyin=int(i["base_price_tiyin"]),
            price_tiyin=int(i["price_tiyin"]),
            duration_minutes=int(i["duration_minutes"]),
            mxik_code=i.get("mxik_code") or "",
            package_code=i.get("package_code") or "",
            vat_percent=i.get("vat_percent"),
        )
        for i in quote["items"]
    ]
    try:
        async with transaction(session):
            # Slot muddati o'tgan eski pending bron'dan bizga o'tdi — u bron shu yerda yopiladi (E8)
            await lifecycle.expire_stale_on_slot(session, req.slot_id, now)
            session.add(booking)
            await session.flush()
            session.add_all(items)
            payloads.created(session, booking, items)
            if status == CONFIRMED:
                payloads.confirmed(session, booking)
            if req.idempotency_key:
                await repo.finish_key(session, req.client_id, req.idempotency_key, booking_id)
            await repo.mark_waitlist_converted(session, req.client_id, req.doctor_id)
    except IntegrityError as exc:
        # uq_bookings_live_slot: eski (to'lov muddati o'tgan, hali expire qilinmagan) bron shu slotda
        raise SlotTaken() from exc
    return booking


def _address_snapshot(address: dict[str, Any]) -> dict[str, Any]:
    keys = ("label", "city", "district", "street", "entrance", "floor", "apartment", "comment", "latitude", "longitude")
    return {k: str(address[k]) if k in ("latitude", "longitude") else address.get(k, "") for k in keys}
