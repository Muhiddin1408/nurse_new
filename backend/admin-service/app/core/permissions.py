"""Admin permission katalogi (ARCHITECTURE 5.2). Format: `<resurs>.<amal>`; `*` — hammasi (superadmin).

Har servis o'z admin endpointida shu nomlardan birini talab qiladi (`require_permission(...)`).
Yangi permission — avval shu yerga (rol tahririda faqat katalogdagilar qabul qilinadi).
"""

ADMINS_MANAGE = "admins.manage"
AUDIT_READ = "audit.read"
SETTINGS_MANAGE = "settings.manage"
WILDCARD = "*"

PERMISSIONS: dict[str, str] = {
    ADMINS_MANAGE: "Admin akkauntlari va rollarini boshqarish",
    AUDIT_READ: "Audit jurnalini ko'rish",
    SETTINGS_MANAGE: "Global sozlamalar (komissiya, bekor qilish siyosati, TTL...)",
    "users.manage": "Foydalanuvchilarni qidirish, bloklash/ochish (auth-service)",
    "notifications.manage": "Bildirishnoma shablonlari va jurnali (notification-service)",
    "files.read": "Maxfiy hujjatlarni ko'rish — har ochilish auditga yoziladi (file-service)",
    "doctors.moderate": "Shifokor arizalarini ko'rib chiqish (catalog-service)",
    "clinics.manage": "Klinikalar va mutaxassisliklar (catalog-service)",
    "bookings.read": "Bronlarni ko'rish (booking-service)",
    "bookings.manage": "Bronlarni boshqarish (booking-service)",
    "payments.read": "To'lovlar, ledger, reconciliation (payment-service)",
    "payments.refund": "Qo'lda refund (payment-service)",
    "payouts.manage": "Payout hisoblash va to'langan deb belgilash (billing-service)",
    "pricing.manage": "Promo kodlar (pricing-service)",
    "reviews.moderate": "Sharhlar moderatsiyasi (review-service)",
    "disputes.resolve": "Nizolarni hal qilish (dispute-service)",
    "search.manage": "Qidiruv indeksini qayta qurish (search-service)",
    "analytics.read": "Hisobotlar (analytics-service)",
}


def validate_permissions(values: list[str]) -> list[str]:
    unknown = sorted(set(values) - set(PERMISSIONS) - {WILDCARD})
    if unknown:
        raise ValueError(f"noma'lum permission: {', '.join(unknown)}")
    return sorted(set(values))
