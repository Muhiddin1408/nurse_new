"""OpenTelemetry + Sentry. `OTEL_EXPORTER_OTLP_ENDPOINT` bo'sh bo'lsa — no-op.

Monolitdagi saboq: manzil kodda qattiq yozilganda lokal/testda mavjud bo'lmagan hostga
uzluksiz urinib, stderr ni to'ldirardi. Kuzatuv vositasi ishlab chiqishga xalaqit qilmasin.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import propagate, trace

logger = logging.getLogger(__name__)
_configured = False


def setup_tracing(
    service_name: str,
    endpoint: str | None,
    *,
    app: Any = None,
    engine: Any = None,
    environment: str = "local",
) -> bool:
    """Tracing'ni yoqadi. Qaytaradi: yoqildimi."""
    global _configured
    if not endpoint:
        return False
    if not _configured:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name, "deployment.environment": environment})
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        AioPikaInstrumentor().instrument()
        _configured = True
        logger.info("tracing enabled", extra={"otlp_endpoint": endpoint})
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready,metrics")
    if engine is not None:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    return True


def setup_sentry(dsn: str | None, *, environment: str, release: str | None = None) -> bool:
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(dsn=dsn, environment=environment, release=release, send_default_pii=False)
    return True


def inject_trace_headers(headers: dict[str, str]) -> dict[str, str]:
    """Joriy span kontekstini (`traceparent`) chiquvchi so'rov/xabar header'lariga qo'shadi."""
    propagate.inject(headers)
    return headers


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
