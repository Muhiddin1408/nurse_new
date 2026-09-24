import hmac


def verify_payme_signature(request) -> bool:
    """Payme so'rovi HTTP Basic Auth orqali keladi: login=Paycom, parol=maxfiy kalit.

    ⚠️ BUNI TEKSHIRMASLIK — ENG XAVFLI XATO shu fazada.
    Tekshirilmasa, hujumchi shunchaki `PerformTransaction` so'rovini o'zi
    yuborib, HAQIQIY PUL TO'LAMASDAN bronni tasdiqlatib olishi mumkin.
    """
    import base64

    from django.conf import settings

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        login, _, key = decoded.partition(":")
    except Exception:  # noqa: BLE001
        return False

    # constant-time solishtirish — timing attack'dan himoya
    return login == "Paycom" and hmac.compare_digest(key, settings.PAYME_SECRET_KEY)

