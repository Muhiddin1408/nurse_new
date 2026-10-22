"""Async engine, sessiya fabrikasi va deklarativ Base (servisning O'Z bazasi)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from medbron_common.db import create_engine, create_session_factory, make_metadata

from app.core.config import get_settings


class Base(DeclarativeBase):
    metadata = make_metadata()


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    if not settings.effective_database_url:
        raise RuntimeError("DATABASE_URL o'rnatilmagan")
    return create_engine(
        settings.effective_database_url, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return create_session_factory(get_engine())


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Tranzaksiyani servis qatlami boshqaradi (`async with session.begin()`)."""
    async with get_session_factory()() as session:
        yield session
