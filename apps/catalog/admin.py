from django.contrib import admin

from apps.catalog.models import PriceRule, ServicePackage, Specialization


@admin.register(Specialization)
class SpecializationAdmin(admin.ModelAdmin):
    """C6 qoidalari va C14 ruscha nomi shu yerda boshqariladi."""

    list_display = ("name", "name_ru", "is_pediatric", "accepts_children", "min_patient_age", "max_patient_age",
                    "allowed_gender")
    search_fields = ("name", "name_ru")


@admin.register(PriceRule)
class PriceRuleAdmin(admin.ModelAdmin):
    """C12 dinamik narx. `percent` manfiy = arzon (kam talab vaqtini to'ldirish),
    musbat = ustama. Qoida SLOT vaqtiga qaraladi."""

    list_display = ("doctor", "name", "weekdays", "start_time", "end_time", "percent", "priority", "is_active")
    list_filter = ("is_active",)
    search_fields = ("doctor__full_name", "name")



@admin.register(ServicePackage)
class ServicePackageAdmin(admin.ModelAdmin):
    """C12 paket. Chegirmani shifokor ko'taradi — payout'dan chiqadi (B8)."""

    list_display = ("name", "doctor", "service", "sessions", "discount_percent", "validity_days", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "doctor__full_name", "service__name")

