"""Swagger (drf-spectacular) uchun umumiy qismlar: teglar va xato javobi."""

from drf_spectacular.utils import OpenApiResponse, inline_serializer
from rest_framework import serializers

TAG_AUTH = "Auth"
TAG_PATIENTS = "Bemor va manzil"
TAG_CATALOG = "Katalog"
TAG_SCHEDULE = "Jadval"
TAG_BOOKING = "Bron"
TAG_PAYMENT = "To'lov"
TAG_DOCTOR = "Shifokor kabineti"
TAG_DOCTOR_SCHEDULE = "Shifokor: jadval"
TAG_DOCTOR_WORK = "Shifokor: qabullar va xizmatlar"
TAG_CLINIC = "Klinika admini"
TAG_DOCTOR_EARNINGS = "Shifokor: daromad va Telegram"
TAG_MODERATION = "Moderatsiya (admin)"

DETAIL = OpenApiResponse(
    response=inline_serializer("Detail", fields={"detail": serializers.CharField()}),
    description="Xato matni `detail` maydonida",
)
