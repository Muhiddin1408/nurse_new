def _client_ip(request) -> str | None:
    """Gateway ortida turganda REMOTE_ADDR gateway'ning IP'si bo'ladi.

    ⚠️ X-Forwarded-For ni faqat O'ZINGIZNING proxy'ingiz ortida ishonch bilan
    o'qing. Aks holda hujumchi uni o'zi yozib yuboradi va IP cheklovini
    butunlay chetlab o'tadi.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")