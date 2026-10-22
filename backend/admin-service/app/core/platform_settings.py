"""Global platforma sozlamalari sxemasi (monolit `conf/settings.py` qiymatlari — standart).

Bo'limlar (`cancellation`, `payment`, ...) — `admin.settings_changed.keys` dagi kalitlar.
PUT da bo'lim ichida faqat yuborilgan maydonlar o'zgaradi; natija qat'iy tekshiriladi
(noma'lum maydon — 422). Pul — tiyin, foiz — butun son,
stavkalar — bazis punkt (1500 = 15.00%), ARCHITECTURE 9 / ADR-0007.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Commission(_Section):
    platform_bp: int = Field(1500, ge=0, le=10000, description="Platforma komissiyasi, bazis punkt")
    payme_fee_bp: int = Field(100, ge=0, le=10000)
    click_fee_bp: int = Field(100, ge=0, le=10000)


class Cancellation(_Section):
    full_refund_hours: int = Field(24, ge=0, le=720)
    partial_refund_hours: int = Field(2, ge=0, le=720)
    partial_refund_percent: int = Field(50, ge=0, le=100)

    @model_validator(mode="after")
    def _order(self) -> Cancellation:
        if self.partial_refund_hours > self.full_refund_hours:
            raise ValueError("partial_refund_hours full_refund_hours dan katta bo'lmasligi kerak")
        return self


class Payment(_Section):
    deposit_percent: int = Field(20, ge=0, le=100)
    force_prepaid_after_no_shows: int = Field(2, ge=0, le=100)


class Schedule(_Section):
    hold_ttl_minutes: int = Field(10, ge=1, le=120)
    min_lead_time_minutes: int = Field(60, ge=0, le=7 * 24 * 60)
    generation_days_ahead: int = Field(30, ge=1, le=180)


class Reminders(_Section):
    """0 — o'sha eslatma o'chiq."""

    first_hours: int = Field(24, ge=0, le=168)
    second_hours: int = Field(2, ge=0, le=48)
    review_after_hours: int = Field(2, ge=0, le=168)


class Pricing(_Section):
    first_booking_discount_percent: int = Field(0, ge=0, le=100)


class Payout(_Section):
    min_amount_tiyin: int = Field(5_000_000, ge=0)
    weekday: int = Field(0, ge=0, le=6, description="0 — dushanba")


class Sla(_Section):
    moderation_hours: int = Field(48, ge=1, le=720)
    dispute_hours: int = Field(48, ge=1, le=720)
    review_window_days: int = Field(30, ge=1, le=365)


class Waitlist(_Section):
    offer_minutes: int = Field(15, ge=1, le=240)
    min_lead_hours: int = Field(2, ge=0, le=72)


class Fiscal(_Section):
    default_mxik_code: str = Field("", max_length=32)
    default_package_code: str = Field("", max_length=32)
    default_vat_percent: int = Field(0, ge=0, le=100)
    tin: str = Field("", max_length=16)


class PlatformSettings(_Section):
    commission: Commission = Commission()
    cancellation: Cancellation = Cancellation()
    payment: Payment = Payment()
    schedule: Schedule = Schedule()
    reminders: Reminders = Reminders()
    pricing: Pricing = Pricing()
    payout: Payout = Payout()
    sla: Sla = Sla()
    waitlist: Waitlist = Waitlist()
    fiscal: Fiscal = Fiscal()


SECTIONS: tuple[str, ...] = tuple(PlatformSettings.model_fields)


def merge(stored: dict[str, Any]) -> PlatformSettings:
    """Bazadagi bo'limlar + kodning standartlari (yangi bo'lim deploy qilinganda bazada hali yo'q)."""
    return PlatformSettings.model_validate({k: v for k, v in stored.items() if k in SECTIONS})
