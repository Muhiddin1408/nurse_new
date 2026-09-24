import logging
import random
import threading
import time

import grpc

from django.conf import settings

from api.notifications.circuit import _breaker
from gen.notification.v1 import notification_pb2 as pb
from gen.notification.v1 import notification_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)

# ⚠️ `from conf.settings import ...` EMAS — `django.conf.settings` orqali.
# Sozlamalar modulini to'g'ridan-to'g'ri import qilish Django'ning sozlama
# qatlamini (env override, test uchun `override_settings`) chetlab o'tadi.
MAX_ATTEMPTS = settings.MAX_ATTEMPTS
BASE_BACKOFF = settings.BASE_BACKOFF
RETRYABLE = settings.RETRYABLE
TIMEOUT_SECONDS = settings.TIMEOUT_SECONDS

_channel: grpc.Channel | None = None

_stub: pb_grpc.NotificationServiceStub | None = None
_init_lock = threading.Lock()


def _get_stub() -> pb_grpc.NotificationServiceStub:
    """Kanalni bir marta ochamiz va qayta ishlatamiz.

    Har so'rovda yangi kanal ochish — sekin va TCP ulanishlarini tugatadi.
    gRPC kanali thread-safe, shuning uchun bitta global nusxa yetarli.
    """
    global _channel, _stub
    if _stub is None:
        with _init_lock:
            if _stub is None:
                from django.conf import settings

                _channel = grpc.insecure_channel(
                    settings.NOTIFICATION_GRPC_ADDR,  # "notification:50051"
                    options=[
                        ("grpc.keepalive_time_ms", 30_000),
                        ("grpc.max_receive_message_length", 4 * 1024 * 1024),
                    ],
                )
                _stub = pb_grpc.NotificationServiceStub(_channel)
    return _stub


def send(
        *,
        idempotency_key: str,
        channel: str,
        recipient: str,
        template: str,
        params: dict[str, str],
        language: str = "uz",
) -> str | None:
    """Bildirishnoma yuboradi.

    Qaytaradi: message_id, yoki None — agar yuborib bo'lmasa.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  DIQQAT: BU FUNKSIYA XATO KO'TARMAYDI (fail-open).                   ║
    ║                                                                       ║
    ║  Sabab: SMS yuborilmagani — bronni bekor qilish uchun asos EMAS.      ║
    ║  Mijoz shifokorga yozildi, puli yechildi, hammasi joyida. SMS         ║
    ║  ketmagani noqulaylik, falokat emas.                                  ║
    ║                                                                       ║
    ║  Bu ATAYLAB qilingan qaror va u har bir bog'liqlik uchun alohida      ║
    ║  qabul qilinadi. To'lov servisi uchun javob teskari bo'ladi — u       ║
    ║  yiqilsa, bronni davom ettirish MUMKIN EMAS.                          ║
    ║                                                                       ║
    ║  Har yangi servis qo'shganingizda o'zingizdan so'rang:                ║
    ║  "bu yiqilsa, asosiy ish davom etadimi?" Javob strategiyani belgilaydi.║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    if not _breaker.allow():
        logger.warning("Bildirishnoma o'tkazib yuborildi: circuit OPEN")
        return None

    request = pb.SendRequest(
        idempotency_key=idempotency_key,
        channel=pb.CHANNEL_SMS if channel == "sms" else pb.CHANNEL_PUSH,
        recipient=recipient,
        template=template,
        params=params,
        language=language,
    )

    last_error: grpc.RpcError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = _get_stub().Send(request, timeout=TIMEOUT_SECONDS)
            _breaker.record_success()
            return response.message_id

        except grpc.RpcError as exc:
            code = exc.code()
            last_error = exc

            if code not in RETRYABLE:
                # Qayta urinish foydasiz — so'rovning o'zi noto'g'ri.
                # Breaker'ni ham qo'zg'atmaymiz: aybdor biz, servis emas.
                logger.error("Bildirishnoma rad etildi: %s %s", code, exc.details())
                return None

            _breaker.record_failure()

            if attempt < MAX_ATTEMPTS:
                # Eksponensial backoff + JITTER.
                #
                # Jitter (tasodifiy qo'shimcha) shart: usiz barcha klientlar
                # bir vaqtda qayta uriniladi va endigina tiklangan servisni
                # darhol qaytadan yiqitadi. Bu "thundering herd" deb ataladi.
                delay = BASE_BACKOFF * (2 ** (attempt - 1))
                time.sleep(delay + random.uniform(0, delay))

    logger.warning(
        "Bildirishnoma yuborilmadi (%s urinish): %s",
        MAX_ATTEMPTS,
        last_error.code() if last_error else "?",
    )
    return None