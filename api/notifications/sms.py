"""
notifications/sms.py — tashqi SMS provayderlari

Tanlov `settings.SMS_PROVIDER` bo'yicha:
    console — faqat logga yozadi (lokal ishlab chiqish, DEBUG da default)
    eskiz   — https://notify.eskiz.uz (prodda default)

Yangi provayder (Play Mobile va h.k.) qo'shish: shu faylda `send(phone, text) -> str`
metodli klass yozib, `get_sms_client()` ga qo'shasiz. Yuqoridagi kod tegilmaydi.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class SmsSendError(Exception):
    """Provayder SMS'ni qabul qilmadi yoki javob bermadi."""


class ConsoleSmsClient:
    def send(self, phone: str, text: str) -> str:
        logger.warning("SMS -> %s: %s", phone, text)
        return "console-fake-id"


class EskizSmsClient:
    """Eskiz.uz API (notify.eskiz.uz).

    TOKEN: login'dan olinadi va ~30 kun yashaydi. Har SMS oldidan login qilish
    sekin va Eskiz uni cheklaydi, shuning uchun token Django keshida saqlanadi —
    prodda Redis, ya'ni barcha podlar bitta tokenni ishlatadi. 401 kelsa
    token eskirgan: bir marta qayta login qilib, so'rovni takrorlaymiz.

    ⚠️ Eskiz'da SMS matni (shablon) moderatsiyadan o'tgan bo'lishi SHART.
    `templates.py` dagi matnlarni Eskiz kabinetida tasdiqlatib oling, aks holda
    SMS rad etiladi. Test akkauntida faqat Eskiz bergan test matni ketadi.
    """

    TOKEN_CACHE_KEY = "sms:eskiz:token"
    TOKEN_TTL_SECONDS = 29 * 24 * 60 * 60
    TIMEOUT_SECONDS = 5

    def __init__(self):
        self.base_url = settings.ESKIZ_BASE_URL.rstrip("/")

    def send(self, phone: str, text: str) -> str:
        response = self._post_sms(phone, text, self._token())
        if response.status_code == 401:
            response = self._post_sms(phone, text, self._token(force_refresh=True))

        if response.status_code >= 400:
            raise SmsSendError(f"Eskiz {response.status_code}: {response.text[:300]}")

        data = response.json()
        message_id = data.get("id")
        if not message_id:
            raise SmsSendError(f"Eskiz javobida id yo'q: {str(data)[:300]}")
        return str(message_id)

    def _post_sms(self, phone: str, text: str, token: str) -> requests.Response:
        try:
            return requests.post(
                f"{self.base_url}/api/message/sms/send",
                headers={"Authorization": f"Bearer {token}"},
                data={
                    # Eskiz raqamni "+" siz kutadi: 998901234567
                    "mobile_phone": phone.lstrip("+"),
                    "message": text,
                    "from": settings.ESKIZ_FROM,
                },
                timeout=self.TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SmsSendError(f"Eskiz'ga ulanib bo'lmadi: {exc}") from exc

    def _token(self, force_refresh: bool = False) -> str:
        if not force_refresh:
            token = cache.get(self.TOKEN_CACHE_KEY)
            if token:
                return token

        try:
            response = requests.post(
                f"{self.base_url}/api/auth/login",
                data={"email": settings.ESKIZ_EMAIL, "password": settings.ESKIZ_PASSWORD},
                timeout=self.TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise SmsSendError(f"Eskiz login: ulanib bo'lmadi: {exc}") from exc

        if response.status_code >= 400:
            # Javob matnida parol bo'lmaydi, lekin baribir qisqartiramiz
            raise SmsSendError(f"Eskiz login {response.status_code}: {response.text[:200]}")

        token = (response.json().get("data") or {}).get("token")
        if not token:
            raise SmsSendError("Eskiz login javobida token yo'q")

        cache.set(self.TOKEN_CACHE_KEY, token, self.TOKEN_TTL_SECONDS)
        return token


def get_sms_client():
    provider = settings.SMS_PROVIDER
    if provider == "console":
        return ConsoleSmsClient()
    if provider == "eskiz":
        return EskizSmsClient()
    raise ValueError(f"Noma'lum SMS_PROVIDER: {provider}")
