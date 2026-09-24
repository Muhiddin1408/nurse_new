"""A11: audit uchun signal'lar — Django admin harakatlari va rol o'zgarishi.

Servis funksiyalari (`api/*`) audit'ni o'zi yozadi (`api.audit.record`).
Bu yerda faqat servisni chetlab o'tadigan yo'llar: admin panel va
`User.role` ni istalgan joydan o'zgartirish.
"""

from django.contrib.admin.models import LogEntry
from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

_ADMIN_ACTIONS = {1: "admin.add", 2: "admin.change", 3: "admin.delete"}


@receiver(post_save, sender=LogEntry, dispatch_uid="audit_admin_logentry")
def _admin_logentry(sender, instance: LogEntry, created, **kwargs):
    if not created:
        return
    from api import audit

    audit.record(
        _ADMIN_ACTIONS.get(instance.action_flag, "admin.action"),
        object_type=instance.content_type.model_class()._meta.label_lower if instance.content_type_id else "",
        object_id=instance.object_id or "",
        after={"repr": instance.object_repr, "change": instance.get_change_message()},
        actor=instance.user,
    )


def _remember_role(sender, instance, **kwargs):
    instance._audit_role = instance.__dict__.get("role")


def _role_changed(sender, instance, created, **kwargs):
    old = getattr(instance, "_audit_role", None)
    if not created and old != instance.role:
        from api import audit

        audit.record("user.role_change", obj=instance, before={"role": old}, after={"role": instance.role})
    instance._audit_role = instance.role


def connect_user_signals():
    from django.contrib.auth import get_user_model

    User = get_user_model()
    post_init.connect(_remember_role, sender=User, dispatch_uid="audit_user_role_init")
    post_save.connect(_role_changed, sender=User, dispatch_uid="audit_user_role_save")
