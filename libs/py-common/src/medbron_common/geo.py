"""Sof geografik funksiyalar (monolit `api/geo.py` dan). Tashqi bog'liqlik yo'q."""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[float, float, float, float]:
    """(min_lat, min_lng, max_lat, max_lng) — indeksli oldindan filtr uchun, keyin haversine."""
    dlat = math.degrees(radius_km / EARTH_RADIUS_KM)
    cos_lat = max(math.cos(math.radians(lat)), 1e-12)
    dlng = math.degrees(radius_km / (EARTH_RADIUS_KM * cos_lat))
    return lat - dlat, lng - dlng, lat + dlat, lng + dlng
