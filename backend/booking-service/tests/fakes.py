"""Upstream servislarning xotiradagi soxta nusxalari (ularning HTTP kontrakti — test_clients.py).

`FakeSchedule` slot holat mashinasini (CAS, lazy expiry, hold_ref) aynan schedule-service kontraktidagidek
modellashtiradi — booking sagasi va kompensatsiyalari shu bilan tekshiriladi.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from medbron_common.money import adjust, bp_of

from app.clients.upstream import Slot, UpstreamRejected

DOCTOR = uuid.UUID("11111111-1111-7111-8111-111111111111")
CLINIC = uuid.UUID("22222222-2222-7222-8222-222222222222")
SERVICE = uuid.UUID("33333333-3333-7333-8333-333333333333")
HOME_SERVICE = uuid.UUID("44444444-4444-7444-8444-444444444444")

PLATFORM: dict[str, Any] = {
    "cancellation": {"full_refund_hours": 24, "partial_refund_hours": 2, "partial_refund_percent": 50},
    "schedule": {"hold_ttl_minutes": 10, "min_lead_time_minutes": 60, "generation_days_ahead": 30},
    "sla": {"review_window_days": 30, "dispute_hours": 48, "moderation_hours": 48},
    "fiscal": {
        "default_mxik_code": "06101001001000000",
        "default_package_code": "1500",
        "default_vat_percent": 0,
        "tin": "",
    },
}


class FakeSettings:
    def __init__(self) -> None:
        self.values = PLATFORM
        self.invalidated = 0

    async def get(self) -> dict[str, Any]:
        return self.values

    async def invalidate(self) -> None:
        self.invalidated += 1


class FakeCatalog:
    def __init__(self) -> None:
        self.doctor: dict[str, Any] = {
            "doctor_id": str(DOCTOR),
            "user_id": str(uuid.uuid4()),
            "status": "approved",
            "bookable": True,
            "full_name": "Dr. Aliyev",
            "accepts_home_visits": True,
            "home_visit_radius_km": 10,
            "home_base": {"lat": 41.3111, "lng": 69.2797},
            "clinic_payment_mode": "prepaid",
            "follow_up_discount_percent": 0,
            "specialization_ids": [],
            "clinic_ids": [str(CLINIC)],
        }
        rules = {
            "is_pediatric": False,
            "accepts_children": True,
            "min_patient_age": None,
            "max_patient_age": None,
            "allowed_gender": "",
        }
        self.services = {
            SERVICE: {
                "id": str(SERVICE),
                "doctor_id": str(DOCTOR),
                "clinic_id": None,
                "specialization_id": str(uuid.uuid4()),
                "name": "Konsultatsiya",
                "name_ru": "",
                "place": "clinic",
                "duration_minutes": 30,
                "price_tiyin": 15_000_000,
                "is_active": True,
                "mxik_code": "",
                "package_code": "",
                "vat_percent": None,
                "patient_rules": rules,
            },
            HOME_SERVICE: {
                "id": str(HOME_SERVICE),
                "doctor_id": str(DOCTOR),
                "clinic_id": None,
                "specialization_id": str(uuid.uuid4()),
                "name": "Uyga chaqiruv",
                "name_ru": "",
                "place": "home",
                "duration_minutes": 60,
                "price_tiyin": 30_000_000,
                "is_active": True,
                "mxik_code": "",
                "package_code": "",
                "vat_percent": None,
                "patient_rules": rules,
            },
        }
        self.clinic_admins: set[tuple[uuid.UUID, uuid.UUID]] = set()

    async def bookability(self, doctor_id: uuid.UUID) -> dict[str, Any] | None:
        return dict(self.doctor) if doctor_id == DOCTOR else None

    async def bookable_services(self, doctor_id: uuid.UUID, ids: list[uuid.UUID]) -> list[dict[str, Any]]:
        return [self.services[i] for i in ids if i in self.services and doctor_id == DOCTOR]

    async def access(self, user_id: uuid.UUID, *, clinic_id: uuid.UUID | None = None) -> dict[str, Any]:
        allowed = clinic_id is not None and (user_id, clinic_id) in self.clinic_admins
        return {"allowed": allowed, "role": "clinic_admin" if allowed else None, "clinic_ids": []}

    async def clinic(self, clinic_id: uuid.UUID) -> dict[str, Any] | None:
        return None


class FakeSchedule:
    def __init__(self) -> None:
        self.slots: dict[uuid.UUID, dict[str, Any]] = {}
        self.calls: list[tuple[str, uuid.UUID]] = []
        self.down = False

    def add(self, start: datetime, *, place: str = "clinic", minutes: int = 30, adjust_percent: int = 0) -> uuid.UUID:
        sid = uuid.uuid4()
        self.slots[sid] = {
            "id": sid,
            "doctor_id": DOCTOR,
            "clinic_id": CLINIC if place == "clinic" else None,
            "place": place,
            "start_at": start,
            "end_at": start + timedelta(minutes=minutes),
            "duration_minutes": minutes,
            "status": "free",
            "hold_ref": None,
            "hold_expires_at": None,
            "price_adjust_percent": adjust_percent,
        }
        return sid

    def _slot(self, sid: uuid.UUID) -> Slot:
        s = dict(self.slots[sid])
        now = datetime.now(UTC)
        if s["status"] == "held" and s["hold_expires_at"] and s["hold_expires_at"] < now:
            s["status"] = "free"
        return Slot(
            id=s["id"],
            doctor_id=s["doctor_id"],
            clinic_id=s["clinic_id"],
            place=s["place"],
            start_at=s["start_at"],
            end_at=s["end_at"],
            duration_minutes=s["duration_minutes"],
            status=s["status"],
            hold_ref=s["hold_ref"],
            hold_expires_at=s["hold_expires_at"],
            price_adjust_percent=s["price_adjust_percent"],
        )

    async def slot(self, sid: uuid.UUID) -> Slot | None:
        if self.down:
            raise RuntimeError("schedule down")
        return self._slot(sid) if sid in self.slots else None

    async def hold(
        self,
        sid: uuid.UUID,
        *,
        hold_ref: uuid.UUID,
        doctor_id: uuid.UUID,
        ttl_seconds: int,
        travel_buffer_minutes: int | None,
    ) -> Slot:
        self.calls.append(("hold", sid))
        s = self.slots.get(sid)
        now = datetime.now(UTC)
        free = s is not None and (
            s["status"] == "free"
            or (s["status"] == "held" and (s["hold_expires_at"] < now or s["hold_ref"] == str(hold_ref)))
        )
        if s is None or s["doctor_id"] != doctor_id or not free:
            raise UpstreamRejected("schedule-service", 409, "SLOT_TAKEN")
        s.update(
            status="held",
            hold_ref=str(hold_ref),
            hold_expires_at=now + timedelta(seconds=ttl_seconds),
            travel_buffer=travel_buffer_minutes,
        )
        return self._slot(sid)

    async def confirm(self, sid: uuid.UUID, hold_ref: uuid.UUID) -> Slot:
        self.calls.append(("confirm", sid))
        if self.down:
            raise RuntimeError("schedule down")
        s = self.slots[sid]
        ok = s["hold_ref"] == str(hold_ref) and (
            s["status"] == "booked" or (s["status"] == "held" and s["hold_expires_at"] >= datetime.now(UTC))
        )
        if not ok:
            raise UpstreamRejected("schedule-service", 409, "HOLD_LOST")
        s.update(status="booked", hold_expires_at=None)
        return self._slot(sid)

    async def extend(self, sid: uuid.UUID, hold_ref: uuid.UUID, minutes: int) -> Slot:
        s = self.slots[sid]
        if s["status"] != "held" or s["hold_ref"] != str(hold_ref):
            raise UpstreamRejected("schedule-service", 409, "HOLD_LOST")
        target = datetime.now(UTC) + timedelta(minutes=minutes)
        if s["hold_expires_at"] < target:
            s["hold_expires_at"] = target
        return self._slot(sid)

    async def release(self, sid: uuid.UUID, hold_ref: uuid.UUID) -> None:
        self.calls.append(("release", sid))
        s = self.slots.get(sid)
        if s is not None and s["hold_ref"] == str(hold_ref):
            s.update(status="free", hold_ref=None, hold_expires_at=None)

    async def free_slots(
        self, doctor_id: uuid.UUID, *, start: datetime, end: datetime, place: str | None, min_minutes: int, limit: int
    ) -> list[Slot]:
        out = [
            self._slot(sid)
            for sid, s in self.slots.items()
            if start <= s["start_at"] < end
            and (place is None or s["place"] == place)
            and s["duration_minutes"] >= min_minutes
        ]
        return sorted([s for s in out if s.status == "free"], key=lambda s: s.start_at)[:limit]


class FakePricing:
    """Monolit qoidalarining soddalashgan ko'rinishi: slot foizi → promo `FREE` (100%) / `BAD` (yaroqsiz) →
    komissiya 15% + provayder 1% → at_clinic shifokor yoki 0 summa, aks holda prepaid."""

    def __init__(self, catalog: FakeCatalog) -> None:
        self.catalog = catalog
        self.quotes: dict[str, dict[str, Any]] = {}
        self.commits: dict[str, str] = {}
        self.commit_conflict = False

    async def quote(self, body: dict[str, Any]) -> dict[str, Any]:
        if body.get("promo_code", "").upper() == "BAD":
            raise UpstreamRejected(
                "pricing-service",
                422,
                "PROMO_INVALID",
                {"code": "PROMO_INVALID", "message": "Promo kod topilmadi", "reason": "not_found"},
            )
        items = []
        for sid in body["service_ids"]:
            svc = self.catalog.services[uuid.UUID(sid)]
            items.append(
                {
                    "service_id": sid,
                    "service_name": svc["name"],
                    "base_price_tiyin": svc["price_tiyin"],
                    "price_tiyin": adjust(svc["price_tiyin"], body["price_adjust_percent"]),
                    "duration_minutes": svc["duration_minutes"],
                    "mxik_code": "",
                    "package_code": "",
                    "vat_percent": None,
                }
            )
        original = sum(i["price_tiyin"] for i in items)
        discount = original if body.get("promo_code", "").upper() == "FREE" else 0
        total = original - discount
        platform_fee, provider_fee = bp_of(total, 1500), bp_of(total, 100)
        mode = (
            "at_clinic"
            if total == 0 or (body["place"] == "clinic" and self.catalog.doctor["clinic_payment_mode"] == "at_clinic")
            else "prepaid"
        )
        quote_id = str(uuid.uuid4())
        quote = {
            "quote_id": quote_id,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "items": items,
            "original_tiyin": original,
            "price_adjust_percent": body["price_adjust_percent"],
            "discount_kind": "promo" if discount else "",
            "discount_tiyin": discount,
            "promo_code": body.get("promo_code", "").upper() if discount else "",
            "total_tiyin": total,
            "money": {
                "total_tiyin": total,
                "platform_fee_tiyin": platform_fee,
                "provider_fee_tiyin": provider_fee,
                "doctor_payout_tiyin": total - platform_fee - provider_fee,
                "discount_tiyin": discount,
                "discount_borne_by": "platform" if discount else "",
            },
            "payment_mode": mode,
            "prepay_tiyin": total if mode == "prepaid" else 0,
            "commission_bp": 1500,
            "provider_fee_bp": 100,
        }
        self.quotes[quote_id] = quote
        return quote

    async def commit(self, quote_id: str, *, booking_id: uuid.UUID, client_id: uuid.UUID) -> dict[str, Any]:
        if self.commit_conflict:
            raise UpstreamRejected("pricing-service", 409, "PROMO_LIMIT_REACHED")
        if quote_id not in self.quotes:
            raise UpstreamRejected("pricing-service", 404, "QUOTE_EXPIRED")
        self.commits[quote_id] = str(booking_id)
        return self.quotes[quote_id]


class FakeUsers:
    def __init__(self) -> None:
        self.patients: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}
        self.addresses: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}

    def add_patient(self, user_id: uuid.UUID, *, birth: str = "1990-05-01", gender: str = "male") -> uuid.UUID:
        pid = uuid.uuid4()
        self.patients[(user_id, pid)] = {
            "id": str(pid),
            "user_id": str(user_id),
            "full_name": "Ali Valiyev",
            "relation": "",
            "birth_date": birth,
            "gender": gender,
            "weight_kg": None,
        }
        return pid

    def add_address(self, user_id: uuid.UUID, *, lat: str = "41.3200", lng: str = "69.2900") -> uuid.UUID:
        aid = uuid.uuid4()
        self.addresses[(user_id, aid)] = {
            "id": str(aid),
            "user_id": str(user_id),
            "label": "Uy",
            "city": "Toshkent",
            "district": "",
            "street": "Amir Temur 1",
            "entrance": "2",
            "floor": "3",
            "apartment": "12",
            "comment": "",
            "latitude": lat,
            "longitude": lng,
            "is_default": True,
        }
        return aid

    async def patient(self, user_id: uuid.UUID, patient_id: uuid.UUID) -> dict[str, Any] | None:
        return self.patients.get((user_id, patient_id))

    async def address(self, user_id: uuid.UUID, address_id: uuid.UUID) -> dict[str, Any] | None:
        return self.addresses.get((user_id, address_id))


class FakeDeps:
    def __init__(self) -> None:
        self.catalog = FakeCatalog()
        self.schedule = FakeSchedule()
        self.pricing = FakePricing(self.catalog)
        self.users = FakeUsers()
        self.settings = FakeSettings()

    async def aclose(self) -> None:
        return None
