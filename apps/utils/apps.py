from django.apps import AppConfig


class UtilsConfig(AppConfig):
    name = 'apps.utils'

    def ready(self):
        from apps.utils import signals

        signals.connect_user_signals()
