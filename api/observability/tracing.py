from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
import logging

logger = logging.getLogger(__name__)

def setup_tracing(service_name: str) -> None:
    """Django ishga tushganda bir marta chaqiriladi (apps.py -> ready()).

    Avtomatik instrumentatsiya (django, requests, grpc, psycopg) bilan
    birga ishlaydi — ya'ni har bir HTTP so'rov, DB so'rovi va servislararo
    chaqiruv AVTOMATIK ravishda trace'ga tushadi, qo'lda kod yozmasdan.

    Kollektor manzili berilmagan bo'lsa, tracing UMUMAN YOQILMAYDI.
    Sabab: manzil qattiq yozilgan bo'lsa (ilgari "jaeger-collector:4317"),
    lokalda va testlarda eksporter mavjud bo'lmagan hostga uzluksiz urinib,
    stderr'ni ogohlantirishlar bilan to'ldiradi. Kuzatuv vositasi
    ishlab chiqish jarayonini xalaqit qilmasligi kerak.
    """
    from django.conf import settings

    endpoint = getattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        logger.debug("OTEL_EXPORTER_OTLP_ENDPOINT berilmagan — tracing o'chirilgan")
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Jaeger (yoki boshqa OTLP kollektori) ga yuboramiz
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)

    # Avtomatik instrumentatsiya
    from opentelemetry.instrumentation.django import DjangoInstrumentor

    DjangoInstrumentor().instrument()

    # psycopg2 faqat PostgreSQL bilan ishlaganda mavjud. Lokalda SQLite
    # ishlatilsa paket yo'q — bu tracing'ni yiqitmasligi kerak, aks holda
    # butun Django ishga tushmay qoladi (bu funksiya AppConfig.ready() dan
    # chaqiriladi). Kuzatuv vositasi kuzatilayotgan tizimni o'ldirmasin.
    try:
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor

        Psycopg2Instrumentor().instrument()
    except ImportError:
        logger.debug("psycopg2 topilmadi — DB instrumentatsiyasi o'tkazib yuborildi")

    logger.info("Tracing yoqildi: %s", service_name)

# Qo'lda span qo'shish — biznes qadamlarini alohida ko'rish uchun:
#
#   tracer = trace.get_tracer(__name__)
#
#   def create_booking(request):
#       with tracer.start_as_current_span("hold_slot"):
#           schedule_services.hold_slot(request.slot_id)
#       with tracer.start_as_current_span("create_payment"):
#           ...
#   # Jaeger'da bu ikki qadam alohida ustun bo'lib ko'rinadi —
#   # qaysi biri sekin ekani darrov ko'zga tashlanadi.


# ---------------------------------------------------------------------------
# STRUKTURALI LOG — trace bilan bog'lash
# ---------------------------------------------------------------------------
#
# Loglar JSON bo'lishi va HAR BIR log qatorida trace_id bo'lishi kerak.
# Shunda Jaeger'da sekin so'rovni topib, uning trace_id'si bo'yicha
# barcha loglarni bir joyga yig'a olasiz. Matnli log bilan bu mumkin emas.
#
#   {"level": "info", "msg": "bron yaratildi", "trace_id": "abc123",
#    "booking_id": "...", "duration_ms": 45}
#
# structlog + python-json-logger bilan sozlanadi.