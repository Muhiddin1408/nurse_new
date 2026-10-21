"""payment-service biznes metrikalari (ARCHITECTURE 10). Alertlar: refund_required, refunds{status=failed|manual},
discrepancies, webhook{result=error} o'sishi."""

from prometheus_client import Counter, Histogram

CHECKOUTS = Counter("payment_checkout_created_total", "Ochilgan to'lov urinishlari", ["provider"])
WEBHOOKS = Counter("payment_webhook_received_total", "Provayder webhook chaqiruvlari", ["method", "result"])
PROVIDER_ERRORS = Counter("payment_provider_errors_total", "Provayder xatolari", ["provider", "code"])
CAPTURED = Counter("payment_succeeded_total", "Yechilgan to'lovlar", ["provider"])
CAPTURE_DURATION = Histogram(
    "payment_duration_seconds",
    "Checkout'dan pul yechilgunicha",
    ["provider"],
    buckets=(10, 30, 60, 120, 300, 600, 1800, 3600),
)
REFUND_REQUIRED = Counter("payment_refund_required_total", "Pul yechildi, bron tasdiqlanmadi", ["provider"])
REFUNDS = Counter("refunds_total", "Refundlar", ["source", "status"])
SAGA_COMPENSATIONS = Counter("saga_compensations_total", "Kompensatsiyalar (provayder qaytardi)", ["kind"])
DISCREPANCIES = Counter("reconciliation_discrepancies_total", "Reconciliation farqlari", ["type"])
