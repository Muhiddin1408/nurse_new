"""Rol asosidagi ruxsat sinflari (D1).

Hammasi holatni BAZADAN tekshiradi — token claim'iga ishonmaydi.
Obyekt darajasidagi egalik view'larning `get_queryset` / loader'larida
(`doctor=request.doctor`) — A4 qoidasi.

Ruxsatlar matritsasi: docs/ROLLAR_VA_RUXSATLAR.md
"""

from rest_framework.permissions import BasePermission

from api import roles


def _authenticated(request) -> bool:
    return bool(request.user and request.user.is_authenticated and request.user.is_active)


class IsPlatformAdmin(BasePermission):
    message = "Faqat platforma admini uchun"

    def has_permission(self, request, view):
        user = request.user
        return _authenticated(request) and (user.is_superuser or user.role == roles.PLATFORM_ADMIN)


class IsDoctor(BasePermission):
    """Shifokor profili bor (har qanday holatda) — onboarding uchun.

    `request.doctor` ni o'rnatadi, view'lar shu orqali ishlaydi.
    """

    message = "Shifokor profili topilmadi"

    def has_permission(self, request, view):
        if not _authenticated(request):
            return False
        from apps.catalog.models import Doctor

        request.doctor = Doctor.objects.filter(user=request.user).first()
        return request.doctor is not None


class IsApprovedDoctor(IsDoctor):
    """Ishlayotgan shifokor: moderatsiyadan o'tgan va to'xtatilmagan."""

    message = "Bu amal faqat tasdiqlangan shifokor uchun"

    def has_permission(self, request, view):
        from apps.catalog.models import Doctor

        return super().has_permission(request, view) and request.doctor.status == Doctor.Status.APPROVED


class IsClinicAdmin(BasePermission):
    """`request.clinic_ids` — foydalanuvchi boshqaradigan klinikalar."""

    message = "Faqat klinika admini uchun"

    def has_permission(self, request, view):
        if not _authenticated(request):
            return False
        from apps.catalog.models import ClinicMembership

        request.clinic_ids = set(
            ClinicMembership.objects.filter(user=request.user, is_active=True).values_list("clinic_id", flat=True)
        )
        return bool(request.clinic_ids)
