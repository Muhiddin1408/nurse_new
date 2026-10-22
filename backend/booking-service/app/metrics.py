"""booking-service biznes metrikalari (ARCHITECTURE 10)."""

from prometheus_client import Counter

BOOKINGS_CREATED = Counter("bookings_created_total", "Yaratilgan bronlar", ["status", "place"])
TRANSITIONS = Counter("booking_transitions_total", "Bron holat o'tishlari", ["to"])
SLOT_CONFLICTS = Counter("slot_hold_conflicts_total", "Slot band bo'lib chiqdi (409 SLOT_TAKEN)")
CONFIRMATION_FAILED = Counter(
    "confirmation_failed_total", "Pul yechildi, lekin bron tasdiqlanmadi (refund kerak)", ["reason"]
)
