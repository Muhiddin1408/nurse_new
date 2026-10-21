"""billing-service biznes metrikalari (ARCHITECTURE 10)."""

from prometheus_client import Counter

ENTRIES = Counter("earning_entries_total", "Daromad daftariga yozuvlar", ["kind"])
PAYOUTS_CREATED = Counter("payouts_created_total", "Hisoblangan payout'lar")
PAYOUTS_PAID = Counter("payouts_paid_total", "To'langan deb belgilangan payout'lar")
ORPHAN_ENTRIES = Counter(
    "earning_entries_orphan_total", "Bron eventi hali kelmagan refund/reversal (shifokor noma'lum)", ["kind"]
)
