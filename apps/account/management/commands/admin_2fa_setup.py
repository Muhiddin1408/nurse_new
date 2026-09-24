"""A12: admin uchun TOTP sozlash. Secret FAQAT shu terminalga chiqadi.

    manage.py admin_2fa_setup +998901234567           # yangi qurilma (eskisi bekor)
    manage.py admin_2fa_setup +998901234567 --remove  # telefon yo'qolganda
"""

from django.core.management.base import BaseCommand, CommandError

from api import audit, totp
from apps.account.models import AdminTOTPDevice, User


class Command(BaseCommand):
    help = "Admin foydalanuvchi uchun TOTP 2FA sozlaydi"

    def add_arguments(self, parser):
        parser.add_argument("phone")
        parser.add_argument("--remove", action="store_true")

    def handle(self, phone, remove=False, **options):
        user = User.objects.filter(phone=phone, is_staff=True).first()
        if user is None:
            raise CommandError("Bunday xodim (is_staff) topilmadi")
        if remove:
            AdminTOTPDevice.objects.filter(user=user).delete()
            audit.record("admin.2fa_remove", obj=user)
            self.stdout.write("2FA o'chirildi")
            return
        secret = totp.new_secret()
        AdminTOTPDevice.objects.update_or_create(user=user, defaults={"secret": secret, "last_step": None})
        audit.record("admin.2fa_setup", obj=user)
        self.stdout.write(f"Secret: {secret}")
        self.stdout.write(f"URI (QR uchun): {totp.provisioning_uri(secret, phone)}")
