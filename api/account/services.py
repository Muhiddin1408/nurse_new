import logging
import re
import secrets
from datetime import timedelta

from django.db import models, transaction

from apps.account.models import User, OtpCode
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from api.notifications.sms import SmsSendError, get_sms_client
from api.notifications.templates import render_template
from api.observability import metrics
from django.core.cache import cache

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Autentifikatsiya xatosi."""


class TooManyRequests(AuthError):
    """Rate limit oshib ketdi."""


class InvalidCode(AuthError):
    """Kod noto'g'ri, eskirgan yoki urinishlar tugagan."""


class InvalidPhone(AuthError):
    """A14: faqat O'zbekiston raqamlari (+998XXXXXXXXX) -> 400.

    SMS pumping: hujumchi xalqaro premium raqamlarga minglab OTP so'ratadi va
    provayder puli bizdan ketadi. Mijozlarimiz O'zbekistonda — boshqa kod kerak emas."""


class SmsUnavailable(AuthError):
    """SMS provayderi kodni qabul qilmadi -> HTTP 503.

    Bildirishnoma SMS'laridan farqli: OTP uchun FAIL-CLOSED. Kod yetmasa
    mijoz kira olmaydi, shuning uchun "Kod yuborildi" deb yolg'on aytmaymiz.
    """


# Rate limit sozlamalari.
# Faza 3 da bularni Redis'ga ko'chiring — hozircha DB count yetarli,
# lekin har so'rovda uchta COUNT so'rovi ketadi va yuklama ostida bu sezilaadi.
RESEND_COOLDOWN_SECONDS = 60
MAX_CODES_PER_PHONE_HOUR = 5
MAX_CODES_PER_IP_HOUR = 20
# A14: kod so'rash limiti (5/soat) × urinishlar (5/kod) = 25 taxmin/soat edi.
# Endi raqam bo'yicha soatiga ko'pi bilan 10 ta XATO tekshiruv, keyin 1 soat blok.
MAX_FAILED_VERIFY_PER_PHONE_HOUR = 10
VERIFY_BLOCK_SECONDS = 3600
_PHONE_RE = re.compile(r"\+998\d{9}")


def request_otp(phone: str, ip_address: str | None = None) -> None:
    """Tasdiqlash kodi yuboradi.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  BU ENDPOINT PUL SARFLAYDI.                                          ║
    ║  Har bir SMS haqiqiy pul, va rate limit qo'yilmasa hujumchi bir      ║
    ║  kechada butun SMS byudjetingizni yoqib yuboradi. Bundan tashqari    ║
    ║  begona odamning telefoniga uzluksiz SMS yog'dirish mumkin bo'ladi.  ║
    ║                                                                       ║
    ║  Uch qatlam himoya: telefon bo'yicha kutish, telefon bo'yicha soatlik ║
    ║  chek, IP bo'yicha soatlik chek.                                      ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    phone = _normalize_phone(phone)
    if not _PHONE_RE.fullmatch(phone):
        metrics.otp_requests.labels(result="rejected_country").inc()
        raise InvalidPhone("Faqat O'zbekiston raqamlari (+998) qabul qilinadi")
    now = timezone.now()

    last = OtpCode.objects.filter(phone=phone).order_by("-created_at").first()
    if last and (now - last.created_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
        remaining = RESEND_COOLDOWN_SECONDS - int((now - last.created_at).total_seconds())
        metrics.otp_requests.labels(result="rate_limited").inc()
        raise TooManyRequests(f"{remaining} soniyadan keyin qayta urinib ko'ring")

    hour_ago = now - timedelta(hours=1)

    if OtpCode.objects.filter(phone=phone, created_at__gte=hour_ago).count() >= MAX_CODES_PER_PHONE_HOUR:
        metrics.otp_requests.labels(result="rate_limited").inc()
        raise TooManyRequests("Juda ko'p urinish. Bir soatdan keyin urinib ko'ring")

    if ip_address and OtpCode.objects.filter(
            ip_address=ip_address, created_at__gte=hour_ago
    ).count() >= MAX_CODES_PER_IP_HOUR:
        raise TooManyRequests("Juda ko'p urinish")

    # Eski kodlarni bekor qilamiz — bir vaqtda faqat bitta kod amal qiladi
    OtpCode.objects.filter(phone=phone, is_used=False).update(is_used=True)

    code = f"{secrets.randbelow(1_000_000):06d}"

    otp = OtpCode.objects.create(
        phone=phone,
        code_hash=make_password(code),
        expires_at=now + timedelta(minutes=OtpCode.TTL_MINUTES),
        ip_address=ip_address,
    )

    try:
        _send_sms(phone, code)
    except SmsSendError as exc:
        # Kod mijozga yetmadi — yozuvni o'chiramiz, aks holda 60 soniyalik
        # cooldown va soatlik limit YETMAGAN SMS uchun sarflanib, mijoz
        # provayder tiklanganda ham darhol qayta so'ray olmasdi.
        otp.delete()
        metrics.otp_requests.labels(result="sms_failed").inc()
        logger.error("OTP SMS yuborilmadi: phone=%s, xato=%s", _mask(phone), exc)
        raise SmsUnavailable("SMS yuborib bo'lmadi. Birozdan keyin urinib ko'ring") from exc
    metrics.otp_requests.labels(result="sent").inc()


def verify_otp(phone: str, code: str) -> tuple[User, bool]:
    """Kodni tekshiradi va foydalanuvchini qaytaradi.

    Qaytaradi: (user, is_new) — is_new True bo'lsa, ilova ism so'rash
    ekranini ko'rsatadi.

    Ro'yxatdan o'tish va kirish BITTA oqim: raqam tizimda bo'lmasa,
    foydalanuvchi shu yerda yaratiladi. Bu mobil ilovalar uchun odatiy
    yondashuv va bitta ekranni kamaytiradi.
    """
    phone = _normalize_phone(phone)
    if not _PHONE_RE.fullmatch(phone):
        raise InvalidPhone("Faqat O'zbekiston raqamlari (+998) qabul qilinadi")
    if _failed_verifies(phone) >= MAX_FAILED_VERIFY_PER_PHONE_HOUR:
        metrics.otp_verify_failed.labels(reason="blocked").inc()
        raise TooManyRequests("Juda ko'p noto'g'ri urinish. Bir soatdan keyin qayta urinib ko'ring")

    otp = (
        OtpCode.objects.filter(phone=phone, is_used=False)
        .order_by("-created_at")
        .first()
    )

    if otp is None or otp.is_expired:
        _register_failed_verify(phone, "expired")
        raise InvalidCode("Kod noto'g'ri yoki muddati o'tgan")

    # Urinishni AVVAL sanaymiz — tekshiruvdan keyin emas.
    # Aks holda xato yuz berganda hisoblagich oshmay qolishi mumkin.
    OtpCode.objects.filter(id=otp.id).update(attempts=models.F("attempts") + 1)
    otp.refresh_from_db()

    if otp.attempts > OtpCode.MAX_ATTEMPTS:
        OtpCode.objects.filter(id=otp.id).update(is_used=True)
        raise InvalidCode("Urinishlar soni tugadi. Yangi kod so'rang")

    if not check_password(code, otp.code_hash):
        _register_failed_verify(phone, "wrong_code")
        raise InvalidCode("Kod noto'g'ri")

    with transaction.atomic():
        OtpCode.objects.filter(id=otp.id).update(is_used=True)

        user = User.objects.filter(phone=phone).first()
        is_new = user is None

        if is_new:
            user = User.objects.create_user(phone=phone, is_phone_verified=True)
        elif not user.is_phone_verified:
            User.objects.filter(id=user.id).update(is_phone_verified=True)
            user.refresh_from_db()

    if not user.is_active:
        raise AuthError("Akkaunt bloklangan")

    logger.info("Kirish: phone=%s, yangi=%s", _mask(phone), is_new)
    return user, is_new


def issue_tokens(user: User, active_role: str | None = None) -> dict[str, str]:
    """JWT juftligi.

    access — qisqa muddatli (15 daqiqa), har so'rovda yuboriladi
    refresh — uzoq muddatli (30 kun), faqat yangilash uchun

    Nega ikkitasi: access o'g'irlansa, u tez eskiradi. refresh esa
    kamdan-kam uzatiladi, ya'ni ushlab olish ehtimoli past.

    settings.py:
        SIMPLE_JWT = {
            "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
            "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
            "ROTATE_REFRESH_TOKENS": True,
            "BLACKLIST_AFTER_ROTATION": True,
        }
    """
    from api import roles as roles_module

    available = roles_module.available_roles(user)
    if active_role is None:
        active_role = roles_module.default_active_role(user, available)
    elif active_role not in available:
        raise AuthError("Bu rol sizga berilmagan")

    refresh = RefreshToken.for_user(user)
    refresh["role"] = user.role  # gateway shu maydonga qarab marshrutlaydi (Faza 2)
    # D1: ilova rol tanlash ekrani uchun. Ruxsat BAZADAN tekshiriladi (api/permissions.py)
    refresh["available_roles"] = available
    refresh["active_role"] = active_role

    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def logout(user: User, refresh: str) -> None:
    """A6: bitta qurilmadan chiqish — refresh token qora ro'yxatga.

    Faqat O'Z tokenini bekor qila oladi: begona refresh berilsa ham xato
    matni bir xil (token mavjudligi oshkor qilinmaydi).
    """
    from rest_framework_simplejwt.exceptions import TokenError

    try:
        token = RefreshToken(refresh)
    except TokenError as exc:
        raise InvalidCode("Token noto'g'ri yoki muddati o'tgan") from exc
    if str(token.get("user_id")) != str(user.id):
        raise InvalidCode("Token noto'g'ri yoki muddati o'tgan")
    token.blacklist()


def logout_all(user: User) -> int:
    """A6: barcha qurilmalardan chiqish (telefon yo'qolganda / raqam o'g'irlanganda).

    Access token'lar 15 daqiqa ichida o'z-o'zidan eskiradi; refresh'lar
    darhol yaroqsiz bo'ladi. Qaytaradi: bekor qilingan tokenlar soni.
    """
    from rest_framework_simplejwt.token_blacklist.models import (
        BlacklistedToken,
        OutstandingToken,
    )

    tokens = OutstandingToken.objects.filter(user=user, expires_at__gt=timezone.now())
    count = 0
    for t in tokens:
        _, created = BlacklistedToken.objects.get_or_create(token=t)
        count += int(created)
    return count


def _verify_key(phone: str) -> str:
    return f"otp:verify_failed:{phone}"


def _failed_verifies(phone: str) -> int:
    return cache.get(_verify_key(phone), 0)


def _register_failed_verify(phone: str, reason: str) -> None:
    """Hisoblagich keshda (prodda Redis — barcha podlarda umumiy), birinchi
    xatodan 1 soat yashaydi. Muvaffaqiyatli kirish uni tozalamaydi: blok
    faqat vaqt bilan ochiladi."""
    key = _verify_key(phone)
    if not cache.add(key, 1, VERIFY_BLOCK_SECONDS):
        try:
            cache.incr(key)
        except ValueError:  # kalit shu lahzada eskirdi
            cache.add(key, 1, VERIFY_BLOCK_SECONDS)
    metrics.otp_verify_failed.labels(reason=reason).inc()
    if _failed_verifies(phone) >= MAX_FAILED_VERIFY_PER_PHONE_HOUR:
        logger.warning("OTP verify bloklandi: phone=%s", _mask(phone))


def _send_sms(phone: str, code: str) -> None:
    """OTP SMS — Kafka orqali EMAS, sinxron: mijoz kodni hozir kutyapti.

    ⚠️ Kodni HECH QACHON ishlab chiqarish loglariga yozmang. Kod faqat
    `SMS_PROVIDER=console` (lokal) da logga tushadi.
    """
    text = render_template("otp", "uz", {"code": code})
    get_sms_client().send(phone, text)


def _normalize_phone(phone: str) -> str:
    """+998 99 756 71 95 -> +998997567195

    Normalizatsiyasiz bitta odam bazada bir necha marta paydo bo'ladi.
    """
    cleaned = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def _mask(phone: str) -> str:
    """Logga to'liq raqam yozmaymiz."""
    return phone[:-6] + "******" if len(phone) > 6 else "***"

