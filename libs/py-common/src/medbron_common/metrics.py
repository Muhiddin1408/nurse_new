"""Prometheus metrikalari (BLUEPRINT 11): HTTP + outbox + consumer.

Biznes metrikalarini servislar o'zi e'lon qiladi (`bookings_created_total` va h.k.),
shu yerdagi umumiy `REGISTRY` ga.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter("http_requests_total", "HTTP so'rovlar soni", ["method", "route", "status"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP so'rov davomiyligi",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

OUTBOX_PENDING = Gauge("outbox_pending", "Chop etilmagan outbox qatorlari")
OUTBOX_PUBLISHED = Counter("outbox_published_total", "Brokerga chiqarilgan eventlar", ["event_type"])
OUTBOX_PUBLISH_ERRORS = Counter("outbox_publish_errors_total", "Publish urinishidagi xatolar", ["event_type"])
OUTBOX_FAILED = Counter("outbox_failed_total", "MAX urinishdan keyin `failed` bo'lgan eventlar", ["event_type"])

EVENTS_CONSUMED = Counter(
    "events_consumed_total",
    "Qabul qilingan eventlar natijasi",
    ["event_type", "result"],  # result: ok | duplicate | retry | dead_letter
)
EVENT_HANDLE_DURATION = Histogram("event_handle_duration_seconds", "Event handler davomiyligi", ["event_type"])
CONSUMER_LAG = Histogram(
    "consumer_lag_seconds",
    "occurred_at dan qayta ishlashgacha o'tgan vaqt",
    ["event_type"],
    buckets=(0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60, 300, 900),
)

UPSTREAM_REQUESTS = Counter(
    "upstream_requests_total", "Boshqa servislarga chiqqan so'rovlar", ["target", "method", "outcome"]
)
CIRCUIT_STATE = Gauge("circuit_breaker_open", "Circuit breaker ochiq (1) yoki yopiq (0)", ["target"])


async def metrics_endpoint(_: Request) -> Response:
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
