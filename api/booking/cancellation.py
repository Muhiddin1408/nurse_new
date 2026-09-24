"""
booking/cancellation.py — bekor qilish siyosati (C2).

SOF FUNKSIYA: bazaga, vaqtga (`now` argument), sozlamaga (`policy` argument)
bog'lanmaydi. Shuning uchun uni 100% test bilan qoplash oson, va `cancel_booking`
hamda `cancellation-preview` endpointi AYNAN bir xil natija ko'rsatadi — mijoz
preview'da ko'rgan summa bekor qilganda qaytadigan summa bilan bir xil bo'ladi.

Vaqt oynalari (default, settings.CANCELLATION_POLICY bilan o'zgartiriladi):

    | Qabulgacha      | Refund | Jarima (shifokorga) |
    |-----------------|--------|---------------------|
    | > 24 soat       | 100%   | 0                   |
    | 2–24 soat       | 50%    | 50%                 |
    | < 2 soat        | 0%     | 100%                |
    | boshlangandan so'ng | bekor qilib bo'lmaydi |

Shifokor / admin / tizim bekor qilsa — har doim 100% refund.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

ACTOR_CLIENT = "client"


@dataclass(frozen=True)
class CancellationPolicy:
    full_refund_before: timedelta = timedelta(hours=24)
    partial_refund_before: timedelta = timedelta(hours=2)
    partial_refund_percent: int = 50


@dataclass(frozen=True)
class CancellationOutcome:
    allowed: bool
    refund_amount: Decimal
    penalty_amount: Decimal
    reason_code: str


def _pct(amount: Decimal, percent: int) -> Decimal:
    return (amount * percent / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def evaluate_cancellation(
    *,
    status: str,
    starts_at: datetime,
    paid_amount: Decimal,
    actor: str,
    now: datetime,
    policy: CancellationPolicy = CancellationPolicy(),
) -> CancellationOutcome:
    zero = Decimal("0.00")
    paid = Decimal(paid_amount).quantize(Decimal("0.01"))

    if status == "pending_payment":
        # Pul olinmagan — qaytaradigan ham, jarima ham yo'q
        return CancellationOutcome(True, zero, zero, "unpaid")

    if status != "confirmed":
        return CancellationOutcome(False, zero, zero, "final_state")

    if now >= starts_at:
        return CancellationOutcome(False, zero, zero, "already_started")

    if actor != ACTOR_CLIENT:
        return CancellationOutcome(True, paid, zero, f"{actor}_cancelled")

    left = starts_at - now
    if left > policy.full_refund_before:
        return CancellationOutcome(True, paid, zero, "full_refund")
    if left > policy.partial_refund_before:
        refund = _pct(paid, policy.partial_refund_percent)
        return CancellationOutcome(True, refund, paid - refund, "partial_refund")
    return CancellationOutcome(True, zero, paid, "late_cancellation")


def policy_from_settings() -> CancellationPolicy:
    from django.conf import settings

    raw = getattr(settings, "CANCELLATION_POLICY", {}) or {}
    return CancellationPolicy(
        full_refund_before=timedelta(hours=raw.get("full_refund_hours", 24)),
        partial_refund_before=timedelta(hours=raw.get("partial_refund_hours", 2)),
        partial_refund_percent=raw.get("partial_refund_percent", 50),
    )
