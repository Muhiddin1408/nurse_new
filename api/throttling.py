"""
throttling.py — umumiy so'rov chegaralari (A7).

Ikki sinf, ikkalasi ham `DEFAULT_THROTTLE_CLASSES` da — ya'ni YANGI endpoint
avtomatik himoyalangan, uni unutib qo'yib bo'lmaydi:

    WriteRateThrottle — POST/PUT/PATCH/DELETE. Foydalanuvchi bo'yicha
        (anonim bo'lsa IP). Default scope `write`; view o'zinikini
        `write_throttle_scope = "booking_create"` bilan beradi.
    AnonReadThrottle — autentifikatsiyasiz GET (katalog, slotlar), IP bo'yicha.

Bu BIZNES cheklovining (max 3 ta to'lanmagan bron, A7/C7) O'RNIGA emas:
throttle akkaunt ko'paytirishga qarshi ojiz, biznes cheklovi esa har akkauntda ishlaydi.

Tashqi webhook'lar (Payme, Telegram) `throttle_classes = []` — provayder
qayta urinishini rad etish pulni yo'qotish demak; ularni IP allowlist va
imzo himoya qiladi.
"""

from rest_framework.throttling import SimpleRateThrottle

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class WriteRateThrottle(SimpleRateThrottle):
    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        self.scope = getattr(view, "write_throttle_scope", "write")
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_rate(self):
        # `scope` allow_request'da aniqlanadi; __init__ dagi chaqiruv uchun default
        if not getattr(self, "scope", None):
            self.scope = "write"
        return super().get_rate()

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"u{request.user.pk}"
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}


class AnonReadThrottle(SimpleRateThrottle):
    scope = "anon_read"

    def allow_request(self, request, view):
        if request.method not in SAFE_METHODS or (request.user and request.user.is_authenticated):
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
