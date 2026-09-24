from apps.account.models import Address


def _clear_other_defaults(user, *, keep) -> None:
    """Bir foydalanuvchida faqat BITTA standart manzil bo'lishi mumkin.

    Bu ham compare-and-set kabi bir qatorlik, lekin muhim: signal yoki
    save() ichida emas, shu yerda aniq yozilgani uchun kod o'qigan odam
    qoidani darhol ko'radi.
    """
    Address.objects.filter(user=user, is_default=True).exclude(id=keep).update(is_default=False)

