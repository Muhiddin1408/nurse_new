from django.contrib import admin

from apps.booking.models import PackageEnrollment, PackageUse, PromoCode, PromoRedemption


class PromoRedemptionInline(admin.TabularInline):
    model = PromoRedemption
    extra = 0
    can_delete = False
    readonly_fields = ("booking", "client", "amount", "is_active", "created_at")


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    """C12: marketing promo kodlari. Har bir o'zgarish audit log'da (A11)."""

    list_display = ("code", "kind", "value", "borne_by", "is_active", "valid_until", "max_uses")
    list_filter = ("is_active", "kind", "borne_by", "first_booking_only")
    search_fields = ("code",)
    inlines = [PromoRedemptionInline]


class PackageUseInline(admin.TabularInline):
    model = PackageUse
    extra = 0
    can_delete = False
    readonly_fields = ("booking", "amount", "is_active", "created_at")


@admin.register(PackageEnrollment)
class PackageEnrollmentAdmin(admin.ModelAdmin):
    """C12: mijozning kursi. Shartlar SNAPSHOT — bu yerdan tuzatilmaydi,
    faqat ko'riladi va kerak bo'lsa yopiladi (`is_active`)."""

    list_display = ("client", "doctor", "service", "discount_percent", "sessions_total",
                    "sessions_left", "expires_at", "is_active")
    list_filter = ("is_active",)
    search_fields = ("client__phone", "doctor__full_name", "service__name")
    readonly_fields = ("package", "client", "doctor", "service", "sessions_total",
                       "discount_percent", "expires_at")
    inlines = [PackageUseInline]

    @admin.display(description="Qolgan seans")
    def sessions_left(self, obj):
        return obj.sessions_left
