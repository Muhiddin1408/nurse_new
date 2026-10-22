"""Migratsiyalar modellarga to'liq mos (autogenerate farqi yo'q) va up/down/up ishlaydi.

Docker talab qiladi (testcontainers): `pytest -m integration`.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

from medbron_common.db import normalize_database_url

pytestmark = pytest.mark.integration


def alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    return cfg


async def _schema_diff(url: str) -> list:
    import app.models  # noqa: F401 — barcha modellar metadata'ga
    from app.core.database import Base

    engine = create_async_engine(normalize_database_url(url))
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda c: compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
            )
    finally:
        await engine.dispose()


def test_migrations_match_models(postgres_url: str) -> None:
    cfg = alembic_config(postgres_url)
    command.upgrade(cfg, "head")
    diff = asyncio.run(_schema_diff(postgres_url))
    assert diff == [], f"modellar va migratsiyalar farq qiladi — yangi migratsiya kerak: {diff}"


def test_downgrade_and_reupgrade(postgres_url: str) -> None:
    cfg = alembic_config(postgres_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
