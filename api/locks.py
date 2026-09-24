"""
Fon joblari uchun distributed lock (E10).

Ikki pod yoki ustma-ust tushgan CronJob bir xil jobni parallel bajarmasligi
kerak — aks holda bitta bron uchun ikkita "bekor bo'ldi" SMS ketadi.

PostgreSQL'da `pg_try_advisory_lock` — qo'shimcha infratuzilma talab qilmaydi
va jarayon o'lsa ulanish bilan birga avtomatik bo'shaydi. Boshqa bazada
(lokal SQLite) kesh orqali `add` — atomik "bo'lmasa qo'y" amali.

    with advisory_lock("expire_bookings") as acquired:
        if not acquired:
            return  # boshqa nusxa ishlayapti
        run()
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager

from django.core.cache import cache
from django.db import connection

# Kesh qulfi jarayon o'lganda ham abadiy qolib ketmasin
CACHE_LOCK_TTL_SECONDS = 15 * 60


def _lock_id(name: str) -> int:
    # pg advisory lock bigint kalit oladi — nomdan barqaror 63-bitli son
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big") & (2**63 - 1)


@contextmanager
def advisory_lock(name: str):
    if connection.vendor == "postgresql":
        key = _lock_id(name)
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
            acquired = cur.fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", [key])
        return

    cache_key = f"lock:{name}"
    acquired = cache.add(cache_key, "1", CACHE_LOCK_TTL_SECONDS)
    try:
        yield acquired
    finally:
        if acquired:
            cache.delete(cache_key)
