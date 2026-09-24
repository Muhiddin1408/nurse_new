"""doctor/digest.py — ertalabki xulosa (D9): "Bugun 6 ta qabul, birinchisi 09:00"."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.utils import timezone

from api.notifications.doctor import notify_doctor
from api.schedule.services import LOCAL_TZ
from apps.booking.models import Booking


def send_daily_summaries() -> int:
    today = timezone.localtime(timezone.now(), LOCAL_TZ).date()
    start = datetime.combine(today, time.min, tzinfo=LOCAL_TZ)
    end = start + timedelta(days=1)
    rows = (Booking.objects.filter(status=Booking.Status.CONFIRMED, slot__start_at__gte=start, slot__start_at__lt=end,
                                   doctor__status="approved")
            .values_list("doctor_id", "slot__start_at").order_by("doctor_id", "slot__start_at"))
    per_doctor: dict = {}
    for doctor_id, start_at in rows:
        per_doctor.setdefault(doctor_id, []).append(start_at)

    sent = 0
    for doctor_id, starts in per_doctor.items():
        # Idempotent: CronJob qayta ishga tushsa ham kuniga bitta xabar
        if not cache.add(f"doctor-digest:{doctor_id}:{today.isoformat()}", "1", 36 * 3600):
            continue
        notify_doctor(doctor_id, "doctor_daily_summary", {
            "count": str(len(starts)), "first": starts[0].astimezone(LOCAL_TZ).strftime("%H:%M"),
        })
        sent += 1
    return sent
