"""
account/deletion.py — foydalanuvchi so'rovi bo'yicha akkauntni o'chirish (A10).

    POST /api/v1/accounts/me/delete

O'chirish = ANONIMLASHTIRISH, jismoniy DELETE emas:
    * bronlar, to'lovlar, buxgalteriya yozuvlari qonuniy muddatgacha qoladi
      (soliq, nizolar) — lekin endi ular hech kimga bog'lanmaydi;
    * telefon, ism, bemorlar ismi, manzillar matni o'chiriladi;
    * barcha sessiyalar bekor qilinadi, akkaunt faolsizlanadi.
Shu raqam bilan keyin kirilsa — butunlay yangi, bo'sh akkaunt yaratiladi.
"""

from __future__ import annotations

import uuid

from django.db import transaction

from api import audit
from api.account.services import logout_all
from apps.account.models import Address, Patient, User


class DeletionBlocked(Exception):
    """O'chirib bo'lmaydi (409)."""


def delete_account(user: User) -> None:
    from apps.booking.models import Booking, WaitlistEntry

    if hasattr(user, "doctor_profile"):
        # Shifokor akkaunti — payout, litsenziya, bemorlar tarixi: qo'llab-quvvatlash orqali
        raise DeletionBlocked("Shifokor akkaunti qo'llab-quvvatlash xizmati orqali o'chiriladi")
    if Booking.objects.filter(client=user, status__in=Booking.ACTIVE_STATUSES).exists():
        raise DeletionBlocked("Avval faol bronlarni bekor qiling yoki qabul yakunlanishini kuting")

    with transaction.atomic():
        audit.record("user.delete", obj=user, actor=user, before={"role": user.role})
        logout_all(user)
        WaitlistEntry.objects.filter(client=user).delete()
        Patient.objects.filter(owner=user).update(full_name="O'chirilgan", relation="", weight_kg=None)
        Address.objects.filter(user=user).update(
            label="", street="—", entrance="", floor="", apartment="", comment="", is_default=False
        )
        user.phone = f"deleted-{uuid.uuid4().hex[:12]}"  # UNIQUE va 20 belgi
        user.full_name = ""
        user.is_active = False
        user.set_unusable_password()
        user.save()
