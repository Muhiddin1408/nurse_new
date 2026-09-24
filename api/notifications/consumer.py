import logging
from datetime import datetime

from confluent_kafka import Consumer, KafkaError
from django.db import close_old_connections, transaction

from apps.notifications.models import ProcessedEvent
from api.events.runner import PERMANENT_ERRORS, NeverStop, dead_letter, decode_envelope
from api.observability.correlation import correlation_scope
from api.notifications import doctor as doctor_notify
from api.notifications import scheduler as notification_scheduler
from api.notifications import services as notification_services

logger = logging.getLogger(__name__)


def run_consumer(stop=None, consumer=None) -> None:
    """`stop` — `GracefulStop` (SIGTERM'da sikl toza tugaydi).
    `consumer` — testlar uchun soxta consumer berish imkoni."""
    stop = NeverStop() if stop is None else stop  # `or` EMAS: to'xtamagan GracefulStop falsy
    consumer = consumer or Consumer(
        {
            "bootstrap.servers": _kafka_addr(),
            "group.id": "notification-service",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,  # QO'LDA commit qilamiz — pastda sababi
        }
    )
    consumer.subscribe(["booking.events", "payment.events", "doctor.notifications"])

    logger.info("Notification consumer ishga tushdi")
    try:
        while not stop:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                _handle_kafka_error(msg)
                continue

            # Uzoq yashaydigan jarayon: baza uzilgan ulanishni yopgan bo'lsa,
            # yangisini ochamiz (aks holda birinchi uzilishdan keyin har
            # xabar "connection already closed" bilan yiqiladi)
            close_old_connections()
            _process_message(msg)

            # ⬇ Xabar MUVAFFAQIYATLI ishlov berilgandan KEYIN commit qilamiz.
            #
            # Agar avtomatik commit (enable.auto.commit=True) ishlatsak,
            # Kafka offset'ni xabar QABUL QILINGANDA belgilaydi — SMS
            # yuborishdan OLDIN. Ishlov berish davomida jarayon o'lib qolsa,
            # xabar YO'QOLADI (consumer uni qayta o'qimaydi).
            #
            # Qo'lda commit — "kamida bir marta" kafolatini beradi:
            # ishlov berilmaguncha offset siljimaydi, demak eng yomon holatda
            # xabar TAKRORLANADI, lekin hech qachon yo'qolmaydi. Shu tufayli
            # idempotentlik majburiy.
            consumer.commit(msg)

    finally:
        consumer.close()


# Bu hodisalar bu servisga tegishli emas — SMS chiqarmaydi.
#
# ATAYLAB ALOHIDA RO'YXAT, `handlers.get() -> None` ga tashlab qo'yilmagan:
# aks holda har bir bron uchun "Noma'lum hodisa turi" ogohlantirishi
# yozilardi. Haqiqiy noma'lum hodisa esa shu shovqin ichida ko'rinmay
# qolardi — ya'ni ogohlantirish o'z ma'nosini yo'qotardi.
#
# `BookingCreated`: mijoz hali to'lov oynasida turibdi, unga "bron
# yaratildi" SMS yuborish ortiqcha — 10 daqiqadan keyin baribir
# tasdiq yoki muddat o'tishi haqida xabar keladi.
#
# `BookingCompleted` / `BookingNoShow`: SMS kerak emas (baholash so'rovi
# C9 — rejalashtirilgan eslatmalar orqali yuboriladi).
IGNORED_EVENTS = frozenset({"BookingCreated", "BookingCompleted", "BookingNoShow"})


CONSUMER_NAME = "notification-service"


def _process_message(msg) -> None:
    envelope = decode_envelope(msg, consumer=CONSUMER_NAME)
    if envelope is None:
        return
    with correlation_scope(envelope.get("correlation_id")):
        _process_envelope(msg, envelope)


def _process_envelope(msg, envelope: dict) -> None:
    event_id = envelope["event_id"]
    event_type = envelope["event_type"]
    data = envelope["data"]

    # ⬇ Filtr `_mark_processing` dan OLDIN: e'tiborsiz qoldiriladigan hodisa
    # uchun `ProcessedEvent` qatori yozishning ma'nosi yo'q. Har bron uchun
    # bittadan bo'lgani sababli, bu jadval bekorga ikki barobar o'sardi.
    if event_type in IGNORED_EVENTS:
        return

    handler = _HANDLERS.get(event_type)
    if handler is None:
        logger.warning("Noma'lum hodisa turi: %s", event_type)
        return

    try:
        # ⬇ Belgilash va ishlov berish BITTA tranzaksiyada. Ilgari belgi avval
        # yozilardi: SMS vaqtinchalik xato bilan yiqilsa, hodisa "ishlangan"
        # bo'lib qolar va qayta o'qilganda SMS HECH QACHON ketmasdi.
        with transaction.atomic():
            if not _mark_processing(event_id):
                logger.info("Hodisa %s allaqachon ishlov berilgan, o'tkazib yuborildi", event_id)
                return
            handler(data)
    except PERMANENT_ERRORS as exc:
        # Payload buzuq (masalan `client_phone` yo'q) — qayta o'qish yordam bermaydi
        dead_letter(CONSUMER_NAME, msg, exc, envelope)


def _build_handlers():
    handlers = {
        "BookingConfirmed": _handle_booking_confirmed,
        "BookingCancelled": _handle_booking_cancelled,
        "BookingExpired": _handle_booking_expired,
        "BookingRescheduled": _handle_booking_rescheduled,
        "RefundRequested": _handle_refund_requested,
        "RefundSucceeded": _handle_refund_succeeded,
        "PayoutPaid": _handle_payout_paid,
        "DoctorLicenseExpiring": _handle_license_expiring,
        "ReviewPublished": _handle_review_published,
        "ClinicInviteSent": _handle_clinic_invite,
    }
    return handlers


def _mark_processing(event_id: str) -> bool:
    """Idempotentlik kafolati: shu event_id allaqachon ishlangan bo'lsa, False.

    ProcessedEvent.event_id UNIQUE — shuning uchun ikki consumer parallel
    ishga tushsa ham (yoki bir xabar ikki marta kelsa ham), faqat BITTASI
    haqiqiy ishni bajaradi.
    """
    from django.db import IntegrityError

    try:
        with transaction.atomic():  # savepoint: tashqi tranzaksiya buzilmasin
            ProcessedEvent.objects.create(event_id=event_id)
        return True
    except IntegrityError:
        return False


def _client_phone(data: dict) -> str:
    """A10: mijoz raqami hodisada EMAS, bazada. Eski (A10 dan oldingi)
    hodisalarda `client_phone` bo'lishi mumkin — ular ham ishlasin.

    Raqam topilmasa KeyError — hodisa dead-letter'ga tushadi (PERMANENT_ERRORS)."""
    if data.get("client_phone"):
        return data["client_phone"]
    from apps.booking.models import Booking

    phone = (
        Booking.objects.filter(id=data.get("booking_id"), client__is_active=True)
        .values_list("client__phone", flat=True)
        .first()
        if data.get("booking_id")
        else None
    )
    if not phone:
        raise KeyError("client_phone: booking_id bo'yicha mijoz topilmadi")
    return phone


def _handle_booking_confirmed(data: dict) -> None:
    notification_services.notify(
        phone=_client_phone(data),
        template="booking_confirmed",
        params={"number": data["booking_number"]},
    )
    # C9: eslatmalar aynan SHU YERDA rejalashtiriladi — bron tasdiqlangan
    # payt. `BookingCreated` da emas: to'lanmagan bronning 90% muddati
    # o'tib ketadi va ularga eslatma yozish jadvalni behuda to'ldirardi.
    if data.get("start_at"):
        notification_scheduler.schedule_booking_reminders(
            booking_id=data["booking_id"],
            start_at=datetime.fromisoformat(data["start_at"]),
            phone=_client_phone(data),
            number=data["booking_number"],
            place=data.get("place", "clinic"),
        )

    # D9: shifokor yangi bron haqida bilmasa, tizim ishlamaydi
    if data.get("doctor_id") and data.get("start_at"):
        doctor_notify.notify_doctor(data["doctor_id"], "doctor_new_booking", {
            "number": data["booking_number"],
            "when": doctor_notify.fmt_when(data["start_at"]),
            "place": "Uy chaqiruvi" if data.get("place") == "home" else "Klinikada",
        })


def _offer_freed_slot(data: dict) -> None:
    """C10: bo'shagan vaqtni navbatdagi birinchi odamga taklif qiladi.

    Bekor qilingan slotni QAYTA SOTADI. Aynan shu yerda — hodisa
    consumer'ida, bron oqimida emas: bekor qilayotgan mijoz boshqa odamga
    SMS ketishini kutib turmasligi kerak.
    """
    from api.booking import waitlist

    if not (data.get("doctor_id") and data.get("start_at")):
        return

    def notify(entry, start_at):
        notification_services.notify(
            phone=entry.client.phone,
            template="waitlist_slot_available",
            params={
                "doctor": entry.doctor.user.full_name or "shifokor",
                "when": doctor_notify.fmt_when(start_at.isoformat()),
            },
        )

    waitlist.notify_slot_freed(
        doctor_id=data["doctor_id"],
        start_at=datetime.fromisoformat(data["start_at"]),
        place=data.get("place", "clinic"),
        notify=notify,
    )


def _cancel_reminders(data: dict) -> None:
    """C9: bron yopildi — kutilayotgan eslatmalarni to'xtatamiz.

    `booking_id` siz ham ishlaydi: eski formatdagi xabar tufayli mijoz
    "bekor qilindi" SMS'ini OLMAY qolishi eslatmadan ko'ra qimmatroq xato.
    Eslatmaning o'zi baribir yuborilmaydi — yuborish paytida bron holati
    qayta tekshiriladi (scheduler.py dagi ikkinchi himoya qatlami).
    """
    booking_id = data.get("booking_id")
    if booking_id:
        notification_scheduler.cancel_booking_reminders(booking_id)


def _handle_booking_cancelled(data: dict) -> None:
    # C9: birinchi himoya qatlami — kutilayotgan eslatmalarni darhol to'xtatish
    _cancel_reminders(data)
    _offer_freed_slot(data)  # C10: bo'shagan vaqt navbatdagiga
    notification_services.notify(
        phone=_client_phone(data),
        template="booking_cancelled",
        params={"number": data["booking_number"]},
    )
    # To'lanmagan bron bekor bo'lsa shifokorni bezovta qilmaymiz — u hech qachon unga ko'rinmagan
    if data.get("previous_status") == "confirmed" and data.get("doctor_id") and data.get("start_at"):
        doctor_notify.notify_doctor(data["doctor_id"], "doctor_booking_cancelled", {
            "number": data["booking_number"], "when": doctor_notify.fmt_when(data["start_at"]),
        })


def _handle_booking_rescheduled(data: dict) -> None:
    """C3: yangi vaqt — mijozga ham, shifokorga ham, eslatmalarga ham.

    Eslatmalarni yangilamasak, mijoz ESKI vaqt haqida eslatma olardi — bu
    ko'chirishning butun foydasini yo'qqa chiqaradi."""
    notification_services.notify(
        phone=_client_phone(data),
        template="booking_rescheduled",
        params={"number": data["booking_number"], "when": doctor_notify.fmt_when(data["start_at"])},
    )
    notification_scheduler.reschedule_booking_reminders(
        booking_id=data["booking_id"],
        start_at=datetime.fromisoformat(data["start_at"]),
        phone=_client_phone(data),
        number=data["booking_number"],
        place=data.get("place", "clinic"),
    )
    # Shifokorning kuni o'zgardi — u bilmasa, eski vaqtda bemorni kutadi
    if data.get("doctor_id"):
        doctor_notify.notify_doctor(data["doctor_id"], "doctor_booking_rescheduled", {
            "number": data["booking_number"], "when": doctor_notify.fmt_when(data["start_at"]),
        })


def _handle_payout_paid(data: dict) -> None:
    doctor_notify.notify_doctor(data["doctor_id"], "doctor_payout_paid", {
        "amount": f"{data['amount']:,}".replace(",", " "),
        "period": f"{data['period_start']} — {data['period_end']}",
    })


def _handle_license_expiring(data: dict) -> None:
    doctor_notify.notify_doctor(data["doctor_id"], "doctor_license_expiring", {"date": data["expires_at"]})


def _handle_clinic_invite(data: dict) -> None:
    """D7: klinika taklifi — shifokor bilmasa, taklif abadiy "invited" qoladi."""
    doctor_notify.notify_doctor(data["doctor_id"], "doctor_clinic_invite", {"clinic": data.get("clinic") or ""})


def _handle_review_published(data: dict) -> None:
    """C11/D9: shifokor yangi sharhni bilsin — javob berish imkoniyati shu."""
    doctor_notify.notify_doctor(data["doctor_id"], "doctor_new_review", {
        "rating": data["rating"], "number": data["booking_number"],
    })


def _handle_booking_expired(data: dict) -> None:
    """Muddati o'tgan bron — matn `booking_cancelled` dan boshqa.

    Mijoz uchun farq muhim: bekor qilingan bron "tugadi", muddati o'tgan
    bron esa "qayta urinib ko'ring" degani. Bitta matn ishlatilsa, to'lay
    olmagan mijoz o'zi bekor qilgan deb o'ylab, qayta bron qilmaydi.
    """
    _cancel_reminders(data)
    _offer_freed_slot(data)
    notification_services.notify(
        phone=_client_phone(data),
        template="booking_expired",
        params={"number": data["booking_number"]},
    )


def _handle_refund_requested(data: dict) -> None:
    """Oddiy bekor qilishda SMS `BookingCancelled` bilan ketgan. Faqat B3 holati —
    pul yechildi, lekin vaqt band bo'lib qoldi — mijozga alohida tushuntiriladi."""
    if data.get("reason") != "booking_unavailable":
        return
    notification_services.notify(
        phone=_client_phone(data),
        template="payment_booking_unavailable",
        params={"number": data["booking_number"]},
    )


def _handle_refund_succeeded(data: dict) -> None:
    notification_services.notify(
        phone=_client_phone(data),
        template="refund_succeeded",
        params={"number": data["booking_number"], "amount": f"{data['amount']:,}".replace(",", " ")},
    )


_HANDLERS = _build_handlers()


def _handle_kafka_error(msg) -> None:
    if msg.error().code() == KafkaError._PARTITION_EOF:
        return  # partitsiya oxiriga yetdik — bu xato emas
    logger.error("Kafka xatosi: %s", msg.error())


def _kafka_addr() -> str:
    from django.conf import settings

    return settings.KAFKA_BOOTSTRAP_SERVERS

