from django.core.management.base import BaseCommand

from api.events.publisher import OutboxPublisher
from api.events.runner import GracefulStop


class Command(BaseCommand):
    help = "Outbox jadvalidan Kafka'ga uzluksiz yetkazib beradi"

    def handle(self, *args, **options):
        OutboxPublisher().run_forever(stop=GracefulStop())
