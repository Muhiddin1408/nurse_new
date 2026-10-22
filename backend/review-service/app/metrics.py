"""review-service biznes metrikalari (ARCHITECTURE 10)."""

from prometheus_client import Counter

REVIEWS = Counter("reviews_submitted_total", "Yozilgan sharhlar", ["status"])
FLAGS = Counter("review_moderation_flags_total", "Avto-moderatsiya bayroqlari", ["flag"])
MODERATED = Counter("reviews_moderated_total", "Moderator qarorlari", ["decision"])
ELIGIBILITY_FALLBACK = Counter(
    "review_eligibility_fallback_total", "Proyeksiyada bron yo'q — booking'dan sinxron so'raldi", ["result"]
)
