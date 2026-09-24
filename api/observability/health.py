import threading

from django.db import connection
from django.http import HttpResponse, JsonResponse

_collector_lock = threading.Lock()
_collector_registered = False


def metrics_view(request):
    """GET /healthz/metrics — Prometheus scrape.

    Ilgari bu endpoint yo'q edi: `api/observability/metrics.py` dagi barcha
    hisoblagichlar jarayon ichida yig'ilib, hech qayerga chiqmasdi.
    """
    global _collector_registered
    from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

    from api.observability.metrics import OutboxCollector, TableSizeCollector

    with _collector_lock:
        if not _collector_registered:
            REGISTRY.register(OutboxCollector())
            # E14: jadvallarning o'sish tendensiyasi
            REGISTRY.register(TableSizeCollector())
            _collector_registered = True
    return HttpResponse(generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)


def liveness(request):
    """GET /healthz/live — "jarayon tirikmi?"

    ATAYLAB HECH NARSANI TEKSHIRMAYDI. Faqat "men javob bera olyapman"
    degani. Agar bu yerda bazani tekshirsak, baza bir soniya sekinlashganda
    Kubernetes butun podni o'chirib qayta ishga tushiradi — bu esa faqat
    ahvolni yomonlashtiradi (qayta ishga tushish sekin, va boshqa podlarga
    yuk oshadi).
    """
    return JsonResponse({"status": "alive"})


def readiness(request):
    """GET /healthz/ready — "hozir so'rov qabul qila olamanmi?"

    Tashqi bog'liqliklarni TEKSHIRADI. Bittasi ishlamasa, 503 qaytaradi va
    Kubernetes bu podga trafik yuborishni to'xtatadi — lekin podni O'CHIRMAYDI.
    Bog'liqlik tiklanganda, keyingi probe muvaffaqiyatli bo'ladi va pod
    avtomatik trafikni qaytaradi.
    """
    checks = {}

    # ── Baza: KRITIK bog'liqlik ────────────────────────────────────────
    # Bazasiz bironta so'rovga javob bera olmaymiz.
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
        healthy = True
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"fail: {exc}"
        healthy = False

    # ── Kafka: KRITIK EMAS ─────────────────────────────────────────────
    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  Kafka o'lgani pod'ni trafikdan CHIQARMAYDI — va bu ataylab.     ║
    # ║                                                                   ║
    # ║  Sabab arxitekturada: API Kafka'ga TO'G'RIDAN-TO'G'RI yozmaydi.  ║
    # ║  U `OutboxEvent` qatorini yozadi (transactional outbox), Kafka'ga ║
    # ║  esa alohida jarayon — `run_outbox_publisher` — yetkazadi.        ║
    # ║  Ya'ni Kafka o'lganda bron yaratish, to'lov va qidiruv BEMALOL    ║
    # ║  ishlayveradi; hodisalar outbox'da navbatda turadi va Kafka       ║
    # ║  tiklanganda o'zi yetkaziladi.                                    ║
    # ║                                                                   ║
    # ║  Agar bu tekshiruv 503 qaytarsa, Kubernetes BARCHA podlarni       ║
    # ║  trafikdan chiqarardi va Kafka nosozligi to'liq API uzilishiga    ║
    # ║  aylanardi — ya'ni tekshiruv o'zi avariyani KENGAYTIRARDI.        ║
    # ║  Bu "fail-open vs fail-closed" tanlovi: SMS/hodisa -> fail-open.  ║
    # ╚══════════════════════════════════════════════════════════════════╝
    try:
        _check_kafka()
        checks["kafka"] = "ok"
    except Exception as exc:  # noqa: BLE001
        # Holat javobda KO'RINADI (monitoring buni ushlaydi), lekin
        # `healthy` ga ta'sir qilmaydi.
        checks["kafka"] = f"degraded: {exc}"

    return JsonResponse({"status": "ready" if healthy else "not_ready", "checks": checks},
                        status=200 if healthy else 503)


# Probe har necha soniyada ishlaydi — har safar yangi Producer yaratish
# qimmat (TCP ulanish + metadata). Bittasini saqlab, qayta ishlatamiz.
_kafka_probe_producer = None

KAFKA_PROBE_TIMEOUT_SECONDS = 2.0


def _check_kafka() -> None:
    """Kafka klaster metadata'sini so'raydi.

    `list_topics` — eng yengil haqiqiy tekshiruv: u broker bilan
    metadata almashadi, lekin hech narsa yozmaydi va topic yaratmaydi.

    ⚠️ TIMEOUT MAJBURIY. Usiz `list_topics` broker javob bermaganda
    standart bo'yicha uzoq kutadi va readiness probe'ni osiltiradi —
    gunicorn worker'i band bo'lib qoladi. Kubernetes probe'ining o'z
    timeout'i bundan KATTA bo'lishi kerak (`k8s/*.yaml` dagi
    `timeoutSeconds`), aks holda natijani hech qachon ko'rmaysiz.
    """
    global _kafka_probe_producer

    if _kafka_probe_producer is None:
        from confluent_kafka import Producer
        from django.conf import settings

        _kafka_probe_producer = Producer(
            {
                "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
                # Probe uchun qayta urinish shart emas — javob darhol kerak
                "socket.timeout.ms": int(KAFKA_PROBE_TIMEOUT_SECONDS * 1000),
            }
        )

    metadata = _kafka_probe_producer.list_topics(timeout=KAFKA_PROBE_TIMEOUT_SECONDS)

    # ⬇ `list_topics` ba'zan istisno ko'tarmasdan bo'sh metadata qaytaradi
    # (masalan DNS ishlaydi, lekin broker'lar yo'q). Buni ham nosozlik
    # deb hisoblaymiz — aks holda tekshiruv yana "yolg'on" gapirardi.
    if not metadata.brokers:
        raise RuntimeError("broker ro'yxati bo'sh")
