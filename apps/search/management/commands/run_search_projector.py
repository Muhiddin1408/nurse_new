"""
    python manage.py run_search_projector

`catalog.events` -> Elasticsearch (`doctors_v1`). Consumer group: `search-projector`.
ES orqada qolib ketgan bo'lsa, avval `reindex_doctors`, keyin shu buyruq.
"""

from django.core.management.base import BaseCommand

from api.events.runner import GracefulStop
from api.search.projector import run_projector


class Command(BaseCommand):
    help = "Katalog hodisalarini Elasticsearch'ga proyeksiya qiladi (Kafka consumer)"

    def handle(self, *args, **options):
        run_projector(stop=GracefulStop())
        self.stdout.write("Search projector to'xtadi")
