def _clickhouse_client():
    import clickhouse_connect
    from django.conf import settings

    return clickhouse_connect.get_client(host=settings.CLICKHOUSE_HOST)


def _kafka_addr() -> str:
    from django.conf import settings

    return settings.KAFKA_BOOTSTRAP_SERVERS