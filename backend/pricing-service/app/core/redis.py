"""Redis: quote'lar (10 daq), shifokor sozlamalari keshi va admin sozlamalari keshi."""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    settings = get_settings()
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL o'rnatilmagan")
    return Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
