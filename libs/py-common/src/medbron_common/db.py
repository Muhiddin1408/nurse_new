"""Async SQLAlchemy yordamchilari. Har servis o'z `Base` va modellarini e'lon qiladi."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# Alembic autogenerate va migratsiyalar barqaror nom olishi uchun yagona konvensiya.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def make_metadata() -> MetaData:
    return MetaData(naming_convention=NAMING_CONVENTION)


def normalize_database_url(url: str) -> str:
    """`postgres://` / `postgresql://` → `postgresql+asyncpg://`."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


def create_engine(url: str, *, pool_size: int = 10, max_overflow: int = 5, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(
        normalize_database_url(url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        **kwargs,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def session_dependency(
    factory: async_sessionmaker[AsyncSession],
) -> Callable[[], AsyncIterator[AsyncSession]]:
    """FastAPI dependency: so'rov uchun sessiya; tranzaksiyani servis qatlami boshqaradi."""

    async def dependency() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    return dependency


@asynccontextmanager
async def transaction(
    session: AsyncSession, *, commit_on: tuple[type[BaseException], ...] = ()
) -> AsyncIterator[AsyncSession]:
    """`session.begin()` kabi, lekin `commit_on` dagi xatolarda ham COMMIT qiladi, keyin xatoni qayta
    ko'taradi. Masalan: noto'g'ri OTP — urinishlar hisoblagichi saqlanishi SHART, javob esa 400;
    qayta ishlatilgan refresh token — oila bekor qilinishi saqlanadi, javob 401.

        async with transaction(session, commit_on=(InvalidCode,)):
            ...
    """
    try:
        yield session
    except commit_on:
        await session.commit()
        raise
    except BaseException:
        await session.rollback()
        raise
    else:
        await session.commit()
