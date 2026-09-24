from elasticsearch import Elasticsearch


def _es_client() -> Elasticsearch:
    from django.conf import settings

    return Elasticsearch(settings.ELASTICSEARCH_URL)


def _kafka_addr() -> str:
    from django.conf import settings

    return settings.KAFKA_BOOTSTRAP_SERVERS