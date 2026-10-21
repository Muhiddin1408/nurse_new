"""Sof qoidalar (I/O yo'q) — monolit `cancellation.py`, `utils.py`, C4, C6 dan; tiyinda."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from medbron_common.geo import haversine_km
from medbron_common.money import percent_of

LOCAL_TZ = ZoneInfo("Asia/Tashkent")
# Chalkash belgilarsiz (0/O, 1/I yo'q)
NUMBER_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def booking_number(now: datetime) -> str:
    """`MB-YYMMDD-XXXX` — sana Toshkent vaqtida (C8)."""
    suffix = "".join(secrets.choice(NUMBER_ALPHABET) for _ in range(4))
    return f"MB-{now.astimezone(LOCAL_TZ):%y%m%d}-{suffix}"


def travel_buffer_minutes(distance_km: float | None) -> int:
    """C4: uy chaqiruvidan keyingi yo'l vaqti. Masofa noma'lum — 30 daq."""
    if distance_km is None:
        return 30
    return max(15, min(90, round(10 + 3 * distance_km)))


def distance_km(home_base: dict[str, Any] | None, address: dict[str, Any]) -> float | None:
    if not home_base:
        return None
    return haversine_km(
        float(home_base["lat"]), float(home_base["lng"]), float(address["latitude"]), float(address["longitude"])
    )


def age_on(birth: date, day: date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def patient_fit_error(rules: dict[str, Any], birth_date: date, gender: str, on: date) -> str | None:
    """C6: mutaxassislikning bemor cheklovlari. None — mos."""
    age = age_on(birth_date, on)
    if rules.get("is_pediatric") and age >= 18:
        return "Bu mutaxassis faqat bolalarni qabul qiladi"
    if not rules.get("is_pediatric") and age < 18 and not rules.get("accepts_children"):
        return "Bu mutaxassis bolalarni qabul qilmaydi"
    if rules.get("min_patient_age") is not None and age < int(rules["min_patient_age"]):
        return "Bemor yoshi mos emas"
    if rules.get("max_patient_age") is not None and age > int(rules["max_patient_age"]):
        return "Bemor yoshi mos emas"
    if rules.get("allowed_gender") and rules["allowed_gender"] != gender:
        return "Bu mutaxassis bemor jinsi bo'yicha cheklangan"
    return None


@dataclass(frozen=True)
class CancellationOutcome:
    allowed: bool
    refund_tiyin: int
    penalty_tiyin: int
    reason_code: str


def evaluate_cancellation(
    *,
    status: str,
    start_at: datetime,
    paid_tiyin: int,
    actor: str,
    now: datetime,
    policy: dict[str, Any],
) -> CancellationOutcome:
    """Monolit C2 tartibi: to'lanmagan → ruxsat, 0; yakuniy → yo'q; boshlangan → yo'q; mijoz bo'lmasa →
    100%; > full_refund_hours → 100%; > partial_refund_hours → partial%; aks holda 0 (kech bekor)."""
    if status == "pending_payment":
        return CancellationOutcome(True, 0, 0, "unpaid")
    if status != "confirmed":
        return CancellationOutcome(False, 0, 0, "final_state")
    if now >= start_at:
        return CancellationOutcome(False, 0, 0, "already_started")
    if actor != "client":
        return CancellationOutcome(True, paid_tiyin, 0, f"{actor}_cancelled")
    left = start_at - now
    if left > timedelta(hours=int(policy["full_refund_hours"])):
        return CancellationOutcome(True, paid_tiyin, 0, "full_refund")
    if left > timedelta(hours=int(policy["partial_refund_hours"])):
        refund = percent_of(paid_tiyin, int(policy["partial_refund_percent"]))
        return CancellationOutcome(True, refund, paid_tiyin - refund, "partial_refund")
    return CancellationOutcome(True, 0, paid_tiyin, "late_cancellation")


def request_hash(payload: dict[str, Any]) -> str:
    import hashlib
    import json

    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
