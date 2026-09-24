"""
geo.py — sof geografik funksiyalar (C4). Tashqi bog'liqlik yo'q, 100% unit-test.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Ikki nuqta orasidagi to'g'ri chiziqli masofa (km).

    Yo'l masofasi emas — shahar ichida real yo'l ~1.3 barobar uzun. Radius
    tekshiruvi uchun yetarli; aniq vaqt kerak bo'lsa marshrut API'si (C4, 2-bosqich).
    `Decimal` ham qabul qilinadi (modeldagi koordinatalar).
    """
    lat1, lng1, lat2, lng2 = map(lambda v: radians(float(v)), (lat1, lng1, lat2, lng2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def bounding_box(lat, lng, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lng, max_lng) — SQL'da arzon oldindan filtr uchun."""
    lat, lng = float(lat), float(lng)
    dlat = radius_km / 111.0
    dlng = radius_km / max(111.0 * cos(radians(lat)), 1e-6)
    return lat - dlat, lat + dlat, lng - dlng, lng + dlng
