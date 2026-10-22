"""ClickHouse sxemasi — versiyalangan, faqat oldinga (Alembic Postgres uchun; ClickHouse'da DDL boshqacha).

`python -m app.cli clickhouse-migrate` (deploy'ning migrate qadami) qo'llanmaganlarini tartib bilan bajaradi;
qo'llanganlari `schema_migrations` da. Har DDL `IF NOT EXISTS` — yarim yo'lda to'xtasa qayta ishga tushirish xavfsiz.

`events_raw` — barcha eventlar (audit.recorded dan tashqari), o'zgarmas tarix. Dublikat (at-least-once) —
`ReplacingMergeTree`: bir xil `(event_type, occurred_at, event_id)` qatorlar birlashadi; hisobotlar `FINAL`
bilan o'qiydi. Tez-tez kerak maydonlar (`booking_id`, `doctor_id`, `clinic_id`) — JSON'dan MATERIALIZED.
"""

from __future__ import annotations

import logging

from app.clients.clickhouse import ClickHouse

logger = logging.getLogger(__name__)

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS events_raw
        (
            event_id       UUID,
            event_type     LowCardinality(String),
            event_version  UInt16,
            producer       LowCardinality(String),
            aggregate_type LowCardinality(String),
            aggregate_id   String,
            occurred_at    DateTime64(3, 'UTC'),
            received_at    DateTime64(3, 'UTC'),
            trace_id       String,
            data           String,
            booking_id     String MATERIALIZED JSONExtractString(data, 'booking_id'),
            doctor_id      String MATERIALIZED JSONExtractString(data, 'doctor_id'),
            clinic_id      String MATERIALIZED JSONExtractString(data, 'clinic_id')
        )
        ENGINE = ReplacingMergeTree(received_at)
        PARTITION BY toYYYYMM(occurred_at)
        ORDER BY (event_type, occurred_at, event_id)
        """,
    ),
    (2, "ALTER TABLE events_raw ADD INDEX IF NOT EXISTS ix_booking booking_id TYPE bloom_filter GRANULARITY 4"),
)


async def migrate(ch: ClickHouse) -> list[int]:
    """Qaytaradi: shu ishga tushishda qo'llangan versiyalar."""
    await ch.command(f"CREATE DATABASE IF NOT EXISTS {ch.database}", database=False)
    await ch.command(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version UInt32, applied_at DateTime DEFAULT now())"
        " ENGINE = MergeTree ORDER BY version"
    )
    applied = {int(r["version"]) for r in await ch.select("SELECT version FROM schema_migrations")}
    done = []
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        await ch.command(sql)
        await ch.insert("schema_migrations", [{"version": version}])
        logger.info("clickhouse migration applied", extra={"version": version})
        done.append(version)
    return done
