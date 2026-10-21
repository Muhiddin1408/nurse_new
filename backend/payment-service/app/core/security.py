"""Autentifikatsiya dependency'lari — libs/py-common dan (servisda qayta yozilmaydi)."""

from medbron_common.security import (
    CurrentAdmin,
    CurrentUser,
    get_current_admin,
    get_current_user,
    get_optional_user,
    require_permission,
    require_roles,
    verify_internal_token,
)

__all__ = [
    "CurrentAdmin",
    "CurrentUser",
    "get_current_admin",
    "get_current_user",
    "get_optional_user",
    "require_permission",
    "require_roles",
    "verify_internal_token",
]
