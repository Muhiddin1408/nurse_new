from django import forms
from django.contrib import admin
from django.core.cache import cache

from api.notifications.templates import TEMPLATES, placeholders
from apps.notifications.models import NotificationTemplate


class NotificationTemplateForm(forms.ModelForm):
    class Meta:
        model = NotificationTemplate
        fields = ["key", "language", "text", "is_active"]
        widgets = {"key": forms.Select(choices=[(k, k) for k in sorted(TEMPLATES)])}

    def clean(self):
        data = super().clean()
        key, text = data.get("key"), data.get("text") or ""
        default = TEMPLATES.get(key)
        if default is None:
            raise forms.ValidationError("Noma'lum shablon kaliti")
        # Kod qaysi parametrlarni beradi — matnda boshqasi bo'lsa SMS yuborilmay qoladi
        allowed = placeholders(default.get("uz", ""))
        extra = placeholders(text) - allowed
        if extra:
            raise forms.ValidationError(f"Noma'lum parametr: {', '.join(sorted(extra))}. Mumkin: {', '.join(sorted(allowed))}")
        return data


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    """C13: xabar matnini deploy'siz o'zgartirish. O'zgarish 60 soniyada kuchga kiradi."""

    form = NotificationTemplateForm
    list_display = ("key", "language", "is_active", "updated_at")
    list_filter = ("language", "is_active")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        cache.delete(f"ntpl:{obj.key}:{obj.language}")
