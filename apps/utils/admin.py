from django.contrib import admin

from apps.utils.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """A11: faqat o'qish. Admin ham jurnalni o'zgartira olmaydi."""

    list_display = ("created_at", "action", "object_type", "object_id", "actor", "actor_role", "ip")
    list_filter = ("action", "object_type", "actor_role")
    search_fields = ("object_id", "correlation_id")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
