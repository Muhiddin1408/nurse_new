"""OTP rate-limit (Redis). Uch qatlam: telefon bo'yicha kutish (cooldown), telefon bo'yicha soatlik chek,
IP bo'yicha soatlik chek + noto'g'ri kodlar uchun blok.

BU ENDPOINT PUL SARFLAYDI: har SMS — haqiqiy pul, va limitsiz hujumchi bir kechada SMS byudjetini
yoqib yuboradi yoki begona telefonga uzluksiz SMS yog'diradi. Redis ishlamasa — fail-closed (503).
Hisoblagich oynasi birinchi INCR dan boshlanadi (keyingi so'rovlar uni uzaytirmaydi).
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.errors import RateLimiterUnavailable, TooManyRequests

HOUR = 3600


@dataclass(frozen=True)
class OtpLimits:
    cooldown_seconds: int
    per_phone_hour: int
    per_ip_hour: int
    failed_verify_per_phone_hour: int


class OtpRateLimiter:
    def __init__(self, redis: Redis, limits: OtpLimits) -> None:
        self._redis = redis
        self.limits = limits

    @staticmethod
    def _keys(phone: str, ip: str | None) -> tuple[str, str, str | None]:
        return f"otp:cooldown:{phone}", f"otp:phone:{phone}", f"otp:ip:{ip}" if ip else None

    async def _incr(self, key: str, ttl: int) -> int:
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        count, _ = await pipe.execute()
        return int(count)

    async def acquire_send(self, phone: str, ip: str | None) -> None:
        """Kod yuborishga ruxsat (yoki 429). Muvaffaqiyatsiz SMS'da `release_send` chaqiriladi."""
        cooldown_key, phone_key, ip_key = self._keys(phone, ip)
        try:
            acquired = await self._redis.set(cooldown_key, "1", ex=self.limits.cooldown_seconds, nx=True)
            if not acquired:
                ttl = await self._redis.ttl(cooldown_key)
                remaining = max(int(ttl), 1)
                raise TooManyRequests(
                    f"{remaining} soniyadan keyin qayta urinib ko'ring",
                    code="OTP_COOLDOWN",
                    extra={"retry_after": remaining},
                    headers={"Retry-After": str(remaining)},
                )
            if await self._incr(phone_key, HOUR) > self.limits.per_phone_hour:
                raise TooManyRequests("Juda ko'p urinish. Bir soatdan keyin urinib ko'ring", code="OTP_PHONE_LIMIT")
            if ip_key and await self._incr(ip_key, HOUR) > self.limits.per_ip_hour:
                raise TooManyRequests("Juda ko'p urinish", code="OTP_IP_LIMIT")
        except RedisError as exc:
            raise RateLimiterUnavailable() from exc

    async def release_send(self, phone: str, ip: str | None) -> None:
        """SMS yetmadi — cooldown va soatlik limit YETMAGAN SMS uchun sarflanmasin."""
        cooldown_key, phone_key, ip_key = self._keys(phone, ip)
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.delete(cooldown_key)
            pipe.decr(phone_key)
            if ip_key:
                pipe.decr(ip_key)
            await pipe.execute()
        except RedisError:
            return  # eng yomon holat: mijoz 60 soniya kutadi

    async def check_verify_allowed(self, phone: str) -> None:
        try:
            failed = await self._redis.get(f"otp:verify_failed:{phone}")
        except RedisError as exc:
            raise RateLimiterUnavailable() from exc
        if failed is not None and int(failed) >= self.limits.failed_verify_per_phone_hour:
            raise TooManyRequests(
                "Juda ko'p noto'g'ri urinish. Bir soatdan keyin qayta urinib ko'ring", code="OTP_VERIFY_BLOCKED"
            )

    async def register_failed_verify(self, phone: str) -> None:
        """Muvaffaqiyatli kirish hisoblagichni TOZALAMAYDI: blok faqat vaqt bilan ochiladi."""
        try:
            await self._incr(f"otp:verify_failed:{phone}", HOUR)
        except RedisError as exc:
            raise RateLimiterUnavailable() from exc
