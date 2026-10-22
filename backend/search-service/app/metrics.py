"""search-service metrikalari (ARCHITECTURE 10). Alert: `search_requests_total{engine="postgres"}` o'sishi —
Elasticsearch ishlamayapti (qidiruv zaxira yo'lda, sekin va imlo xatosini kechirmaydi)."""

from prometheus_client import Counter, Histogram

REQUESTS = Counter("search_requests_total", "Qidiruv so'rovlari", ["kind", "engine"])
LATENCY = Histogram("search_latency_seconds", "Qidiruv javobi", ["kind"], buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5))
DOCS_WRITTEN = Counter("search_documents_written_total", "ES'ga yozilgan hujjatlar", ["kind"])
STALE_EVENTS = Counter("search_stale_events_total", "Eskirgan (tartibsiz) eventlar — qo'llanmadi", ["aspect"])
REINDEX_RUNS = Counter("search_reindex_runs_total", "Reindex natijalari", ["status"])
