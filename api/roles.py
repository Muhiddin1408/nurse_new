"""
roles.py — ko'p rolli akkaunt (D1).

Bitta odam bir vaqtda mijoz HAM shifokor bo'lishi mumkin (shifokor ham
kasal bo'ladi). Shuning uchun rol `User.role` ning bitta qiymati emas —
u MA'LUMOTDAN hisoblanadi:

    client          — har doim
    doctor          — `Doctor` profili bor
    clinic_admin    — faol `ClinicMembership` bor
    platform_admin  — `User.role == platform_admin` yoki superuser

Token'da `available_roles` va `active_role` bor; ilova rol tanlash ekranini
ko'rsatadi, `switch-role` bilan almashtiradi.

⚠️ Token claim'i faqat INTERFEYS uchun ishora. Ruxsat sinflari holatni
BAZADAN tekshiradi: shifokor to'xtatilgan bo'lsa, eski token'dagi
`active_role=doctor` hech narsa bermaydi.
"""

from __future__ import annotations

CLIENT = "client"
DOCTOR = "doctor"
CLINIC_ADMIN = "clinic_admin"
PLATFORM_ADMIN = "platform_admin"

ALL_ROLES = (CLIENT, DOCTOR, CLINIC_ADMIN, PLATFORM_ADMIN)


def available_roles(user) -> list[str]:
    from apps.catalog.models import ClinicMembership, Doctor

    roles = [CLIENT]
    if Doctor.objects.filter(user=user).exists():
        roles.append(DOCTOR)
    if ClinicMembership.objects.filter(user=user, is_active=True).exists():
        roles.append(CLINIC_ADMIN)
    if user.is_superuser or user.role == PLATFORM_ADMIN:
        roles.append(PLATFORM_ADMIN)
    return roles


def default_active_role(user, roles: list[str]) -> str:
    return user.role if user.role in roles else CLIENT


def get_active_role(request) -> str:
    """Token'dagi `active_role`; token yo'q bo'lsa (sessiya/test) — `User.role`."""
    token = getattr(request, "auth", None)
    if token is not None and hasattr(token, "get"):
        role = token.get("active_role")
        if role:
            return role
    return getattr(request.user, "role", CLIENT)
