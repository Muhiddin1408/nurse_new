from api.observability.correlation import get_correlation_id
from apps.utils.models import OutboxEvent


def publish(*, topic: str, key: str, event_type: str, payload: dict) -> None:
    """Hodisani outbox'ga yozadi.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  MUHIM: bu funksiya Kafka'ga TO'G'RIDAN-TO'G'RI yozmaydi.            ║
    ║  U faqat OutboxEvent qatorini yaratadi.                               ║
    ║                                                                       ║
    ║  Shuning uchun bu funksiya CHAQIRUVCHINING tranzaksiyasi ICHIDA       ║
    ║  ishlaydi — alohida `transaction.atomic()` OCHMAYDI. Agar ochsa,      ║
    ║  butun maqsad yo'qoladi: domen yozuvi va hodisa yana ikki xil         ║
    ║  tranzaksiyaga bo'linib qoladi.                                       ║
    ║                                                                       ║
    ║  TO'G'RI chaqirish:                                                   ║
    ║      with transaction.atomic():                                      ║
    ║          Booking.objects.filter(id=id).update(status=CONFIRMED)       ║
    ║          events.publish(topic="booking.events", ...)                  ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    OutboxEvent.objects.create(
        topic=topic,
        key=key,
        event_type=event_type,
        payload=payload,
        correlation_id=get_correlation_id(),
    )