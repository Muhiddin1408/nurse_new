"""Alembic (async). Migratsiya deploy'ning ALOHIDA qadami (BLUEPRINT 9) — container CMD'da emas:

docker compose --profile migrate run --rm analytics-service-migrate
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from medbron_common.db import create_engine

import app.models  # noqa: F401 — barcha modellar metadata'ga yuklansin
from app.core.config import get_settings
from app.core.database import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    override = config.attributes.get("database_url")
    url = override or get_settings().effective_database_url
    if not url:
        raise RuntimeError("DATABASE_URL o'rnatilmagan")
    return url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_url(), pool_size=1, max_overflow=0)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
