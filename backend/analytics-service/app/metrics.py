"""analytics-service metrikalari. Alertlar: `analytics_ingest_errors_total` o'sishi, `analytics_ingest_lag_seconds`."""

from prometheus_client import Counter, Histogram

INGESTED = Counter("analytics_events_ingested_total", "ClickHouse'ga yozilgan eventlar", ["event_type"])
SKIPPED = Counter("analytics_events_skipped_total", "Yozilmagan xabarlar", ["reason"])
INGEST_ERRORS = Counter("analytics_ingest_errors_total", "ClickHouse'ga yozish xatolari (partiya qayta yoziladi)")
BATCH_SIZE = Histogram("analytics_ingest_batch_size", "Partiya hajmi", buckets=(1, 10, 50, 100, 250, 500, 1000))
LAG = Histogram(
    "analytics_ingest_lag_seconds",
    "Event yuz bergandan ClickHouse'ga yozilgunicha",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 300, 1800),
)
