"""dispute-service biznes metrikalari (ARCHITECTURE 10). Alert: `disputes_sla_breached_total` o'sishi."""

from prometheus_client import Counter

OPENED = Counter("disputes_opened_total", "Ochilgan nizolar", ["reason"])
RESOLVED = Counter("disputes_resolved_total", "Hal qilingan nizolar", ["resolution", "doctor_liable"])
SLA_BREACHED = Counter("disputes_sla_breached_total", "SLA muddatida hal qilinmagan nizolar")
CLOSED = Counter("disputes_closed_total", "Yopilgan nizolar (refund yakunlandi yoki rad etildi)", ["resolution"])
