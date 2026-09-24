from django.apps import AppConfig


class BookingConfig(AppConfig):
    name = 'apps.booking'

    def ready(self):
        from api.observability.tracing import setup_tracing
        setup_tracing(service_name="booking-service")