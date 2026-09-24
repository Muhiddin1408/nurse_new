"""
    python manage.py run_notification_consumer

`booking.events` / `payment.events` -> SMS. Consumer group: `notification-service`.
Bir nechta nusxa ishga tushirish mumkin — Kafka partitsiyalarni taqsimlaydi,
`ProcessedEvent` esa takroriy SMS'dan himoya qiladi.
"""

from django.core.management.base import BaseCommand

from api.events.runner import GracefulStop
from api.notifications.consumer import run_consumer


class Command(BaseCommand):
    help = "Bron hodisalarini o'qib SMS yuboradi (Kafka consumer)"

    def handle(self, *args, **options):
        run_consumer(stop=GracefulStop())
        self.stdout.write("Notification consumer to'xtadi")
