"""Operatsion buyruqlar: `python -m app.cli clickhouse-migrate` (deploy'ning migrate qadami, Alembic'dan keyin)."""

from __future__ import annotations

import argparse
import asyncio
import sys

from medbron_common.logging import configure_logging

from app.clickhouse.schema import migrate
from app.clients.clickhouse import ClickHouse
from app.core.config import get_settings


async def _clickhouse_migrate() -> list[int]:
    ch = ClickHouse.from_settings()
    try:
        return await migrate(ch)
    finally:
        await ch.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("clickhouse-migrate", help="ClickHouse sxemasini yangilaydi (idempotent)")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(f"{settings.service_name}-cli", settings.log_level)
    if args.command == "clickhouse-migrate":
        applied = asyncio.run(_clickhouse_migrate())
        print(f"clickhouse: qo'llandi {applied or 'hech narsa (yangi)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
