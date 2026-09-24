"""
booking/services.py — Faza 1

Bron yaratish oqimi.

BU FAYLNING KELAJAGI:
    Hozir bu oddiy Django funksiyalari va hammasi bitta tranzaksiyada ishlaydi —
    ya'ni ACID bepul beriladi. Faza 4 da Jadval va To'lov alohida servisga
    chiqqanda, aynan shu fayl SAGA ORKESTRATORIGA aylanadi va bepul ACID
    yo'qoladi. Fayl oxiridagi izohda nima o'zgarishi yozilgan.

    Shuning uchun hozirdanoq qadamlarni ANIQ AJRATIB yozamiz — keyin har bir
    qadam alohida tarmoq chaqiruviga aylanadi.

MODUL CHEGARASI:
    catalog va schedule modellarini import qilmaymiz — faqat ularning
    services.py sidagi funksiyalarni chaqiramiz.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.booking.models import Booking, BookingItem, PackageUse, PromoRedemption
from api.booking import payment_mode, waitlist
from api.booking import discounts, packages, time_pricing
from api.booking.pricing import current_rates, split_amount, split_with_discount
from api.booking.cancellation import CancellationOutcome, evaluate_cancellation, policy_from_settings
from api.catalog import services as catalog_services
from api.payments import refunds as refund_services
from api.events import services as events
from api.observability import metrics
from api.pii import client_hash
from api.payments import fiscal
from api import audit
from api.patients import services as patient_services
from api.schedule import cache as schedule_cache
from api.schedule import services as schedule_services
from api.schedule.services import SlotNotAvailable

logger = logging.getLogger(__name__)


class BookingError(Exception):
    """Bron yaratishdagi biznes xatosi."""


class InvalidBookingRequest(BookingError):
    """So'rov noto'g'ri: xizmat mos emas, manzil yo'q va h.k."""


class SlotTaken(BookingError):
    """Slot band (409). C10: javobga muqobil vaqtlar va navbat taklifi qo'shiladi.

    Oddiy `BookingError` dan farqi — u o'zi bilan KONTEKST olib yuradi
    (qaysi shifokor, qaysi vaqt, qaysi joy). Usiz xato ishlovchisi
    muqobillarni topa olmas va mijoz uchun oqim shu yerda tugardi."""

    def __init__(self, message: str, *, doctor_id: UUID, start_at, place: str):
        super().__init__(message)
        self.doctor_id = doctor_id
        self.start_at = start_at
        self.place = place


# ---------------------------------------------------------------------------
# Kiruvchi ma'lumot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookingRequest:
    """Bron so'rovi.

    dataclass ishlatamiz, chunki Faza 4 da bu to'g'ridan-to'g'ri gRPC
    message'ga aylanadi. Argumentlarni tarqoq holda uzatsangiz, o'sha paytda
    hamma joyni qayta yozishga to'g'ri keladi.
    """

    client_id: UUID
    patient_id: UUID
    doctor_id: UUID
    slot_id: UUID
    service_ids: list[UUID]
    address_id: UUID | None = None
    comment: str = ""
    promo_code: str = ""  # C12


# ---------------------------------------------------------------------------
# Asosiy oqim
# ---------------------------------------------------------------------------


@metrics.booking_duration.time()
def create_booking(request: BookingRequest) -> Booking:
    """Bron yaratadi va slotni vaqtincha band qiladi.

    Qadamlar (Faza 4 da har biri saga qadami bo'ladi):
        1. Tekshiruvlar        — lokal, tarmoqsiz
        2. Xizmat narxlarini olish   -> catalog
        3. Slotni HELD qilish        -> schedule
        4. Booking + BookingItem yozish
        5. To'lovni boshlash         -> payments  (Faza 4 da qo'shiladi)

    Natija: PENDING_PAYMENT holatidagi bron. To'lov 10 daqiqada kelmasa,
    `expire_pending_bookings` uni bekor qiladi.
    """
    try:
        services, buffer_minutes = _validate_and_load_services(request)
    except InvalidBookingRequest:
        # ⬇ Metrika BIZNES sababini yozadi, texnik xatoni emas. Grafanada
        # "nega bron yaratilmayapti" savoliga aynan shu `reason` javob beradi.
        metrics.bookings_failed.labels(reason="validation").inc()
        raise

    # ⬇ Bitta tranzaksiya — bu MONOLIT IMTIYOZI.
    # Ichkarida biror narsa yiqilsa, slot ham avtomatik FREE ga qaytadi,
    # chunki hold_slot ning UPDATE'i ham shu tranzaksiyada.
    # Faza 4 da bu kafolat YO'QOLADI va o'rniga kompensatsiya yozasiz.
    with transaction.atomic():
        try:
            slot = schedule_services.hold_slot(
                request.slot_id, doctor_id=request.doctor_id, buffer_minutes=buffer_minutes
            )
        except SlotNotAvailable as exc:
            metrics.bookings_failed.labels(reason="slot_taken").inc()
            raise _slot_taken(request.slot_id, request.doctor_id, services[0].place) from exc

        _expire_stale_booking_on_slot(request.slot_id)

        # C12 dinamik narx: slot vaqtiga qarab xizmat narxi tuzatiladi.
        # Bu chegirmadan OLDIN bo'lishi shart — chegirma tuzatilgan narxdan
        # hisoblanadi, aks holda "arzon soat" ikki marta hisoblanib ketadi.
        adjust_percent = time_pricing.percent_for_slot(
            request.doctor_id, slot.start_at, schedule_services.LOCAL_TZ
        )
        prices = time_pricing.adjusted_prices(services, adjust_percent)
        original = sum(prices.values(), Decimal("0"))
        # C12: eng foydali BITTA chegirma (promo / birinchi bron / takroriy / paket)
        discount = _discount_for(request, original, prices)
        total = original - discount.amount
        split = split_with_discount(original, discount.amount, discount.borne_by, *current_rates())

        # B10: rejim shifokor sozlamasi, qabul joyi va mijozning no-show
        # tarixidan kelib chiqadi. `at_clinic` bo'lsa bron DARHOL tasdiqlanadi
        # — to'lov kutilmaydi, ya'ni konversiya yo'qotadigan qadam yo'q.
        plan = payment_mode.plan_from_settings(
            total=total,
            is_home_visit=slot.clinic_id is None,
            doctor_mode=catalog_services.get_clinic_payment_mode(request.doctor_id),
            client_id=request.client_id,
        )
        initial_status = (
            Booking.Status.PENDING_PAYMENT
            if plan.is_paid_online
            else Booking.Status.CONFIRMED
        )

        booking = Booking.objects.create(
            commission_rate=split.commission_rate,
            provider_fee_rate=split.provider_fee_rate,
            platform_fee=split.platform_fee,
            provider_fee=split.provider_fee,
            doctor_payout=split.doctor_payout,
            number=_generate_number(),
            client_id=request.client_id,
            patient_id=request.patient_id,
            doctor_id=request.doctor_id,
            slot_id=request.slot_id,
            address_id=request.address_id,
            status=initial_status,
            payment_mode=plan.mode,
            prepay_amount=plan.prepay_amount,
            total_price=total,
            original_price=original,
            price_adjust_percent=adjust_percent,
            discount_amount=discount.amount,
            discount_kind=discount.kind,
            promo_code=discount.code,
            discount_borne_by=discount.borne_by,
            client_comment=request.comment,
        )
        if discount.promo is not None:
            PromoRedemption.objects.create(
                promo=discount.promo, booking=booking, client_id=request.client_id, amount=discount.amount
            )
        # C12 paket: seans AYNAN shu yerda yechiladi — bron qatori bilan
        # bitta tranzaksiyada. Bron yiqilsa seans ham yechilmaydi.
        if discount.enrollment is not None:
            PackageUse.objects.create(
                enrollment=discount.enrollment, booking=booking, amount=discount.amount
            )
            packages.close_exhausted(discount.enrollment.id)

        BookingItem.objects.bulk_create(
            [
                BookingItem(
                    booking=booking,
                    service_id=s.id,
                    # ⬇ SNAPSHOT: nom, narx va davomiylik bron paytidagi holatda
                    # muzlatiladi. Klinika ertaga narxni oshirsa, bu bron va
                    # undan chiqadigan hisobotlar o'zgarmaydi.
                    service_name=s.name,
                    # C12: slot vaqtiga tuzatilgan narx — mijoz aynan shuni
                    # to'laydi, fiskal chek ham shundan chiqadi.
                    price=prices[s.id],
                    duration_minutes=s.duration_minutes,
                    **fiscal.item_codes(s),
                )
                for s in services
            ]
        )

        # ⬇ Hodisa bulk_create'dan KEYIN: payload `booking.items.first()` dan
        # xizmat kontekstini (mutaxassislik, place) oladi, ya'ni item'lar
        # allaqachon yozilgan bo'lishi shart.
        #
        # Bu hodisa SMS chiqarmaydi (mijoz hali to'lov oynasida turibdi) —
        # u faqat analitika uchun: usiz "bron boshlandi, to'lanmadi"
        # voronkasi ko'rinmaydi va konversiya maxraji noma'lum qoladi.
        _publish_booking_event(
            booking, "BookingCreated", Booking.Status.PENDING_PAYMENT
        )

        # B10: klinikada to'lanadigan bron to'lov kutmaydi — slot darhol
        # BOOKED bo'ladi va mijozga tasdiq SMS'i ketadi. Hold qoldirilsa,
        # 10 daqiqadan keyin tasdiqlangan bronning sloti bo'shab ketardi.
        if initial_status == Booking.Status.CONFIRMED:
            schedule_services.confirm_slot(request.slot_id)
            _publish_booking_event(
                booking, "BookingConfirmed", Booking.Status.CONFIRMED
            )

        # Slot FREE -> HELD bo'ldi, ya'ni u endi bo'sh emas.
        # ⬇ Kunni `slot` dan olamiz: `hold_slot` uni allaqachon qaytardi,
        # shuning uchun `get_slot_day` ning qo'shimcha SELECT'i shart emas.
        # Bu tizimning eng issiq yozish yo'li — tranzaksiya ichidagi har bir
        # ortiqcha so'rov qulf ushlab turish vaqtini uzaytiradi.
        _invalidate_slot_cache(
            request.doctor_id,
            request.slot_id,
            day=slot.start_at.astimezone(schedule_services.LOCAL_TZ).date(),
        )

    # C10: mijoz shu shifokorga yozildi — navbat o'z vazifasini bajardi.
    # Yopilmasa, u keyingi bo'shagan slot haqida ham SMS olaverardi.
    waitlist.mark_converted(client_id=request.client_id, doctor_id=request.doctor_id)

    metrics.bookings_created.labels(
        doctor_specialization=_specialization_label(services),
        place=services[0].place,
    ).inc()

    logger.info("Bron yaratildi: %s, summa=%s", booking.number, total)
    return booking


def _discount_for(request: BookingRequest, original: Decimal,
                  service_prices: dict[UUID, Decimal]) -> discounts.Discount:
    try:
        return discounts.best_discount(
            client_id=request.client_id,
            doctor_id=request.doctor_id,
            total=original,
            promo_code=request.promo_code,
            follow_up_percent=catalog_services.get_follow_up_discount_percent(request.doctor_id),
            service_prices=service_prices,
        )
    except discounts.PromoInvalid as exc:
        metrics.bookings_failed.labels(reason="validation").inc()
        raise InvalidBookingRequest(str(exc)) from exc


def _slot_taken(slot_id: UUID, doctor_id: UUID, place: str) -> SlotTaken:
    """C10: "band" javobiga muqobil vaqt va navbat taklifi uchun kontekst."""
    slot = schedule_services.get_slot_for_booking(slot_id)
    return SlotTaken(
        "Bu vaqt allaqachon band qilingan",
        doctor_id=doctor_id,
        start_at=slot.start_at if slot else None,
        place=place,
    )


def confirm_booking(booking_id: UUID) -> Booking:
    """To'lov muvaffaqiyatli — bronni tasdiqlaydi.

    IDEMPOTENT: allaqachon tasdiqlangan bronni qayta tasdiqlash xato emas.
    Faza 3 da bu funksiyani Kafka consumer chaqiradi va Kafka xabarni
    takrorlab yuborishi mutlaqo normal hol.
    """
    booking = _get_booking(booking_id)

    if booking.status == Booking.Status.CONFIRMED:
        return booking  # allaqachon tasdiqlangan

    if booking.status != Booking.Status.PENDING_PAYMENT:
        raise BookingError(
            f"Bron {booking.number} tasdiqlab bo'lmaydi: holati {booking.status}"
        )

    with transaction.atomic():
        schedule_services.confirm_slot(booking.slot_id)
        Booking.objects.filter(id=booking_id).update(
            status=Booking.Status.CONFIRMED, updated_at=timezone.now()
        )
        # ⬇ Hodisa BIR XIL tranzaksiyada yoziladi (Transactional Outbox).
        # Agar bu qator tashqariga chiqarilsa, bron tasdiqlanib hodisa
        # yozilmay qolishi mumkin — mijoz SMS olmaydi va analitika
        # noto'g'ri bo'ladi. Ikkalasi birga bo'ladi yoki ikkalasi ham yo'q.
        _publish_booking_event(booking, "BookingConfirmed", Booking.Status.CONFIRMED)
        # Slot HELD -> BOOKED. Hold ham keshda "bo'sh emas" edi, lekin hold
        # muddati o'tishi mumkin edi — tasdiqdan keyin holat yakuniy.
        _invalidate_slot_cache(booking.doctor_id, booking.slot_id)

    booking.refresh_from_db()
    logger.info("Bron tasdiqlandi: %s", booking.number)
    return booking


FINAL_STATES = {
    Booking.Status.CANCELLED,
    Booking.Status.EXPIRED,
    Booking.Status.COMPLETED,
    Booking.Status.NO_SHOW,
}


CANCELLATION_DENIED_MESSAGES = {
    "already_started": "Qabul boshlangan — bekor qilib bo'lmaydi",
    "final_state": "Bu bronni bekor qilib bo'lmaydi",
}


def preview_cancellation(booking_id: UUID, actor: str = "client") -> CancellationOutcome:
    """C2: mijoz bekor qilishdan OLDIN qancha qaytishini ko'radi.

    `cancel_booking` bilan AYNAN bir xil hisob — ikkalasi shu funksiyani ishlatadi.
    """
    booking = _get_booking(booking_id)
    slot = schedule_services.get_slot_for_booking(booking.slot_id)
    return evaluate_cancellation(
        status=booking.status,
        starts_at=slot.start_at,
        paid_amount=refund_services.get_paid_amount(booking.id),
        actor=actor,
        now=timezone.now(),
        policy=policy_from_settings(),
    )


def cancel_booking(
    booking_id: UUID,
    reason: str = "",
    *,
    actor: str = "client",
    issue_refund: bool = True,
) -> Booking:
    """Bronni bekor qiladi va slotni bo'shatadi.

    Bu ayni paytda SAGA KOMPENSATSIYA AMALI ham — to'lov yiqilganda
    shu funksiya chaqiriladi. Shuning uchun idempotent bo'lishi shart.

    Bekor qilish siyosati (C2) qo'llanadi: qabul boshlangan bo'lsa -> 409;
    to'langan bo'lsa siyosat bo'yicha refund shu tranzaksiyada navbatga
    qo'yiladi (B4). `issue_refund=False` — pul provayder tomonida allaqachon
    qaytarilgan (Payme CancelTransaction -2).

    ⚠️ To'lov MUDDATI O'TGANDA buni chaqirmang — `expire_booking` bor.
    Farqi analitikada hal qiluvchi: bekor qilish "mijoz fikridan qaytdi",
    muddat o'tishi esa "mijoz to'lay olmadi".
    """
    booking = _get_booking(booking_id)
    if booking.status in FINAL_STATES:
        return booking

    outcome = preview_cancellation(booking_id, actor)
    if not outcome.allowed:
        raise BookingError(CANCELLATION_DENIED_MESSAGES.get(outcome.reason_code, "Bekor qilib bo'lmaydi"))

    def _refund():
        if issue_refund and outcome.refund_amount > 0:
            refund_services.request_refund(
                booking_id=booking_id,
                amount=outcome.refund_amount,
                reason=outcome.reason_code,
                initiated_by=actor,
            )

    return _release_and_finalize(
        booking_id,
        new_status=Booking.Status.CANCELLED,
        event_type="BookingCancelled",
        reason=reason,
        log_message="Bron bekor qilindi",
        in_transaction=_refund,
    )


def expire_booking(booking_id: UUID) -> Booking:
    """To'lov muddati o'tdi — bronni EXPIRED qiladi va slotni bo'shatadi.

    `cancel_booking` dan faqat ikki narsa bilan farq qiladi: yakuniy holat
    (`expired`) va hodisa turi (`BookingExpired`). Qolgan hammasi bir xil,
    shuning uchun ikkalasi ham `_release_and_finalize` ustida quriladi.

    NEGA ALOHIDA FUNKSIYA (ilgari `cancel_booking` + qo'shimcha `UPDATE` edi):
        1. Hodisa `BookingCancelled` chiqardi, baza esa `expired` bo'lardi —
           ya'ni hodisa va holat BIR-BIRIGA ZID edi. Analitika hodisadan
           qurilgani uchun `countIf(status = 'expired')` har doim 0 qaytarardi.
        2. Muddati o'tgan bronlar `is_cancelled = 1` bilan yozilib,
           `cancellation_rate_by_specialization` ni sun'iy oshirardi.
        3. Ikkita `UPDATE` orasida bron qisqa vaqt `cancelled` holatida
           turardi — o'sha oynada o'qigan har kim noto'g'ri holat ko'rardi.
    """
    return _release_and_finalize(
        booking_id,
        new_status=Booking.Status.EXPIRED,
        event_type="BookingExpired",
        reason="To'lov muddati o'tdi",
        log_message="Bron muddati o'tdi",
    )


def _release_and_finalize(
    booking_id: UUID,
    *,
    new_status: str,
    event_type: str,
    reason: str,
    log_message: str,
    in_transaction=None,
) -> Booking:
    """`cancel_booking` va `expire_booking` uchun umumiy tana.

    `in_transaction` — holat o'zgarishi bilan BITTA tranzaksiyada bajariladigan
    qo'shimcha amal (masalan refund navbatga qo'yish).

    IDEMPOTENT: yakuniy holatdagi bron o'zgarmaydi va YANGI HODISA
    CHIQARMAYDI. Bu Kafka uchun shart — xabar takrorlanishi normal hol.
    """
    booking = _get_booking(booking_id)

    if booking.status in FINAL_STATES:
        return booking

    with transaction.atomic():
        schedule_services.release_slot(booking.slot_id)
        # ⬇ BITTA UPDATE. Ilgari muddat o'tishi ikki qadamda bajarilardi
        # (avval CANCELLED, keyin EXPIRED) — oraliqda holat noto'g'ri edi.
        Booking.objects.filter(id=booking_id).update(
            status=new_status,
            cancelled_reason=reason,
            updated_at=timezone.now(),
        )
        _publish_booking_event(booking, event_type, new_status)
        # C12: bekor / muddati o'tgan bron promo limitini egallab turmasin
        discounts.release_redemption(booking_id)
        if in_transaction is not None:
            in_transaction()
        # ⬇ ENG MUHIM invalidatsiya: slot yana FREE bo'ldi. Buni o'tkazib
        # yuborsak, bo'shagan vaqt 30 soniyagacha "band" ko'rinib turadi va
        # mijoz uni bron qila olmaydi — ya'ni sotuv yo'qoladi.
        _invalidate_slot_cache(booking.doctor_id, booking.slot_id)

    booking.refresh_from_db()
    logger.info("%s: %s, sabab=%s", log_message, booking.number, reason)
    return booking


# ---------------------------------------------------------------------------
# C3: ko'chirish (reschedule)
# ---------------------------------------------------------------------------

# Cheklovsiz ko'chirish = slot spekulyatsiyasi: bitta bron bilan bir necha
# vaqtni navbatma-navbat egallab turish mumkin bo'lardi.
MAX_RESCHEDULES = 2


def reschedule_booking(
    booking_id: UUID, new_slot_id: UUID, *, actor: str = "client"
) -> Booking:
    """Bronni boshqa vaqtga ko'chiradi. Narx O'ZGARMAYDI.

    NEGA BEKOR QILISH + QAYTA BRON EMAS: mijoz pul qaytishini kutib turishi,
    o'sha orada yangi slot band bo'lib qolishi va butun oqimni qaytadan
    o'tishi kerak bo'lardi. Amalda mijozning bir qismi shu yerda yo'qoladi —
    ko'chirish bekor qilishning bir qismini saqlab qoladi.

    TARTIB MUHIM — avval yangi slotni band qilamiz, keyin eskisini
    bo'shatamiz. Teskari tartibda eski slot bo'sh qolgan lahzada uni boshqa
    mijoz olib ketishi mumkin, yangisi esa band chiqsa — mijoz ikkala
    vaqtdan ham ayrilardi.

    Narx snapshot'i (`BookingItem`) TEGILMAYDI: shifokor ertaga narxini
    oshirsa ham, ko'chirilgan bron eski narxda qoladi. Aks holda ko'chirish
    yashirin narx oshirish vositasiga aylanardi.
    """
    booking = _get_booking(booking_id)

    if booking.status not in (Booking.Status.PENDING_PAYMENT, Booking.Status.CONFIRMED):
        raise BookingError("Bu bronni ko'chirib bo'lmaydi")

    if booking.rescheduled_count >= MAX_RESCHEDULES:
        raise BookingError(
            f"Bronni ko'pi bilan {MAX_RESCHEDULES} marta ko'chirish mumkin. "
            "Yangi bron qiling."
        )

    if new_slot_id == booking.slot_id:
        raise InvalidBookingRequest("Bu allaqachon shu bronning vaqti")

    old_slot = schedule_services.get_slot_for_booking(booking.slot_id)
    new_slot = _validate_new_slot(booking, old_slot, new_slot_id)

    # ⬇ C2 bilan BIR XIL oyna: qabulga 24 soatdan kam qolganda shifokorning
    # kuni allaqachon shu bron atrofida qurilgan. Oyna ikkita bo'lsa,
    # mijoz "bekor qilish pulli, ko'chirish bepul" teshigidan foydalanib
    # oxirgi daqiqada ko'chirar va jarima to'lamasdi.
    time_left = old_slot.start_at - timezone.now()
    if time_left < policy_from_settings().full_refund_before:
        raise BookingError("Qabulga kam vaqt qoldi — ko'chirib bo'lmaydi")

    was_confirmed = booking.status == Booking.Status.CONFIRMED

    with transaction.atomic():
        try:
            schedule_services.hold_slot(
                new_slot_id,
                doctor_id=booking.doctor_id,
                buffer_minutes=_travel_buffer_for_booking(booking) if new_slot.is_home_visit else None,
            )
        except SlotNotAvailable as exc:
            metrics.bookings_failed.labels(reason="slot_taken").inc()
            raise BookingError("Bu vaqt allaqachon band qilingan") from exc

        _expire_stale_booking_on_slot(new_slot_id)

        # To'langan bron ko'chirilsa yangi slot darhol BOOKED bo'ladi —
        # aks holda hold 10 daqiqada tugab, to'langan vaqt bo'shab ketardi.
        if was_confirmed:
            schedule_services.confirm_slot(new_slot_id)

        schedule_services.release_slot(booking.slot_id)

        Booking.objects.filter(id=booking_id).update(
            slot_id=new_slot_id,
            rescheduled_count=booking.rescheduled_count + 1,
            updated_at=timezone.now(),
        )

        booking.refresh_from_db()
        _publish_booking_event(booking, "BookingRescheduled", booking.status)

        # Ikkala kun ham keshdan chiqadi: eski vaqt bo'shadi, yangisi band bo'ldi
        _invalidate_slot_cache(
            booking.doctor_id,
            booking.slot_id,
            day=old_slot.start_at.astimezone(schedule_services.LOCAL_TZ).date(),
        )
        _invalidate_slot_cache(
            booking.doctor_id,
            new_slot_id,
            day=new_slot.start_at.astimezone(schedule_services.LOCAL_TZ).date(),
        )

    metrics.bookings_rescheduled.inc()
    logger.info(
        "Bron ko'chirildi: %s, %s -> %s (%s)",
        booking.number, old_slot.start_at, new_slot.start_at, actor,
    )
    return booking


def _validate_new_slot(booking: Booking, old_slot, new_slot_id: UUID):
    """Yangi slot eskisining O'RNINI BOSA OLADIMI.

    Ko'chirish — vaqtni almashtirish, boshqa bron emas. Shuning uchun
    shifokor, qabul joyi va davomiylik mos kelishi shart: mos kelmasa
    mijoz yangi bron qiladi va narx qaytadan hisoblanadi.
    """
    if old_slot is None:
        raise BookingError("Bronning joriy vaqti topilmadi")

    new_slot = schedule_services.get_slot_for_booking(new_slot_id)
    # A2 qoidasi: "topilmadi" va "begona" uchun xato matni bir xil
    if new_slot is None or new_slot.doctor_id != booking.doctor_id:
        raise InvalidBookingRequest("slot_id noto'g'ri")

    if new_slot.start_at <= timezone.now() + timedelta(
        minutes=schedule_services.MIN_LEAD_TIME_MINUTES
    ):
        raise InvalidBookingRequest("Bu vaqt juda yaqin")

    # Uy xizmati klinika slotiga (yoki aksincha) tushmasin — yo'l buferi
    # va manzil mantiqi butunlay boshqacha
    if new_slot.is_home_visit != old_slot.is_home_visit:
        raise InvalidBookingRequest("Yangi vaqt qabul joyiga mos emas")

    total_duration = sum(
        booking.items.values_list("duration_minutes", flat=True)
    )
    if total_duration > new_slot.duration_minutes:
        raise InvalidBookingRequest(
            f"Bu xizmatlar uchun {total_duration} daqiqa kerak, "
            f"tanlangan vaqt {new_slot.duration_minutes} daqiqa"
        )

    # C7: bemorda yangi vaqtda boshqa qabul bo'lmasin
    live = Q(status=Booking.Status.CONFIRMED) | Q(
        status=Booking.Status.PENDING_PAYMENT, slot__hold_expires_at__gte=timezone.now()
    )
    clash = (
        Booking.objects.filter(
            live,
            patient_id=booking.patient_id,
            slot__start_at__lt=new_slot.end_at,
            slot__end_at__gt=new_slot.start_at,
        )
        .exclude(id=booking.id)
        .exists()
    )
    if clash:
        raise BookingError("Bu bemorda shu vaqtda boshqa qabul bor")

    return new_slot


# ---------------------------------------------------------------------------
# C1: bron siklini yopish — completed / no_show
# ---------------------------------------------------------------------------

# Shifokor belgilamasa, qabul tugaganidan shuncha vaqt o'tib avtomatik `completed`
AUTO_COMPLETE_AFTER_HOURS = 24
AUTO_COMPLETE_BATCH_SIZE = 500


def _load_started_confirmed(booking_id: UUID) -> tuple[Booking, bool]:
    """(bron, allaqachon_yakunlanganmi). Faqat boshlangan `confirmed` qabul yakunlanadi."""
    booking = _get_booking(booking_id)
    if booking.status in (Booking.Status.COMPLETED, Booking.Status.NO_SHOW):
        return booking, True
    if booking.status != Booking.Status.CONFIRMED:
        raise BookingError(
            f"Bron {booking.number} yakunlab bo'lmaydi: holati {booking.status}"
        )
    slot = schedule_services.get_slot_for_booking(booking.slot_id)
    if slot is None or slot.start_at > timezone.now():
        raise BookingError("Qabul hali boshlanmagan")
    return booking, False


def complete_booking(booking_id: UUID, *, by: str, note: str = "") -> Booking:
    """Qabul bo'lib o'tdi. IDEMPOTENT: yakunlangan bron o'zgarmaydi.

    `completed` — payout, sharh, daromad hisobi va no-show statistikasining kaliti.
    Manbalar: shifokor (asosiy), tizim (24 soatdan keyin zaxira), admin.
    """
    booking, done = _load_started_confirmed(booking_id)
    if done:
        return booking

    with transaction.atomic():
        updated = Booking.objects.filter(
            id=booking_id, status=Booking.Status.CONFIRMED
        ).update(
            status=Booking.Status.COMPLETED,
            completed_at=timezone.now(),
            completed_by=by,
            note=note,
            updated_at=timezone.now(),
        )
        if updated:
            _publish_booking_event(booking, "BookingCompleted", Booking.Status.COMPLETED)
            if by != Booking.Actor.SYSTEM:
                audit.record("booking.complete", obj=booking, before={"status": booking.status},
                             after={"status": Booking.Status.COMPLETED, "by": by})

    booking.refresh_from_db()
    logger.info("Qabul yakunlandi: %s, by=%s", booking.number, by)
    return booking


def mark_no_show(booking_id: UUID, *, absent: str, reported_by: str, note: str = "") -> Booking:
    """Qabul bo'lmadi. `absent` — KIM kelmadi: `client` yoki `doctor`.

    Mijoz kelmadi -> pul shifokorga qoladi. Shifokor kelmadi -> to'liq refund
    (B4 refund oqimi orqali). Ikkalasi bitta holatda, lekin `no_show_by`
    bilan ajratiladi.
    """
    if absent not in (Booking.Actor.CLIENT, Booking.Actor.DOCTOR):
        raise InvalidBookingRequest("absent: client yoki doctor bo'lishi kerak")

    booking, done = _load_started_confirmed(booking_id)
    if done:
        return booking

    with transaction.atomic():
        updated = Booking.objects.filter(
            id=booking_id, status=Booking.Status.CONFIRMED
        ).update(
            status=Booking.Status.NO_SHOW,
            no_show_by=absent,
            completed_by=reported_by,
            completed_at=timezone.now(),
            note=note,
            updated_at=timezone.now(),
        )
        if updated:
            _publish_booking_event(booking, "BookingNoShow", Booking.Status.NO_SHOW)
            audit.record("booking.no_show", obj=booking, before={"status": booking.status},
                         after={"status": Booking.Status.NO_SHOW, "absent": absent, "by": reported_by})
            if absent == Booking.Actor.DOCTOR:
                # Shifokor kelmadi -> mijozga to'liq refund
                refund_services.request_refund(
                    booking_id=booking_id,
                    amount=refund_services.get_paid_amount(booking_id),
                    reason="doctor_no_show",
                    initiated_by=Booking.Actor.SYSTEM,
                )

    booking.refresh_from_db()
    logger.info("No-show: %s, kelmadi=%s", booking.number, absent)
    return booking


def auto_complete_bookings() -> int:
    """Zaxira: shifokor belgilamagan tugagan qabullar -> `completed` (by=system).

    Shifokorlar unutadi, lekin pul oqimi (payout) to'xtamasligi kerak.
    """
    threshold = timezone.now() - timedelta(hours=AUTO_COMPLETE_AFTER_HOURS)
    ids = list(
        Booking.objects.filter(
            status=Booking.Status.CONFIRMED, slot__end_at__lte=threshold
        ).values_list("id", flat=True)[:AUTO_COMPLETE_BATCH_SIZE]
    )
    count = 0
    for booking_id in ids:
        try:
            complete_booking(booking_id, by=Booking.Actor.SYSTEM)
            count += 1
        except BookingError:
            logger.exception("Avto-yakunlashda xato: %s", booking_id)
    if count:
        logger.info("Avtomatik yakunlangan qabullar: %s ta", count)
    return count


def expire_pending_bookings() -> int:
    """To'lanmagan bronlarni EXPIRED qiladi. Har daqiqada ishlaydigan job.

    `schedule.release_expired_holds` bilan birga ishlaydi — ular bir-birini
    dublikat qilmaydi: bu yerda BRON holati, u yerda SLOT holati tozalanadi.
    """
    stale_bookings = Booking.objects.filter(
        status=Booking.Status.PENDING_PAYMENT,
        slot_id__in=schedule_services.expired_hold_slot_ids(),
    ).values_list("id", flat=True)

    count = 0
    for booking_id in list(stale_bookings):
        expire_booking(booking_id)
        count += 1

    if count:
        logger.info("Muddati o'tgan bron: %s ta", count)
    return count


# ---------------------------------------------------------------------------
# Ichki yordamchilar
# ---------------------------------------------------------------------------


BOOKING_EVENTS_TOPIC = "booking.events"


def _invalidate_slot_cache(doctor_id: UUID, slot_id: UUID, day=None) -> None:
    """Slot bandligi o'zgardi — shu shifokorning shu kunidagi keshini o'chiradi.

    `day` berilsa, u ishlatiladi va bazaga bormaymiz. Chaqiruvchi slotni
    allaqachon qo'lida ushlab turgan bo'lsa (masalan `hold_slot` qaytargan),
    shu yo'ldan yuring — tranzaksiya ichida ortiqcha SELECT qilmaslik uchun.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  `transaction.on_commit` ATAYLAB ishlatilgan — keshni darhol         ║
    ║  o'chirmaymiz.                                                        ║
    ║                                                                       ║
    ║  Agar kesh tranzaksiya ICHIDA o'chirilsa, commit'gacha bo'lgan        ║
    ║  oynada parallel so'rov "MISS" ko'radi, bazadan HALI ESKI holatni     ║
    ║  o'qiydi (o'zgarish hali ko'rinmaydi) va uni keshga QAYTA yozadi.     ║
    ║  Natijada kesh 30 soniyaga eskisidan ham yomonroq holatda qotib       ║
    ║  qoladi — o'chirish o'z maqsadiga teskari ishlaydi.                   ║
    ║                                                                       ║
    ║  Rollback bo'lsa on_commit umuman ishlamaydi — bu ham to'g'ri:        ║
    ║  hech narsa o'zgarmagan bo'lsa, kesh ham eskirmagan.                   ║
    ╚══════════════════════════════════════════════════════════════════════╝

    Tranzaksiyadan tashqarida chaqirilsa, Django `on_commit` ni darhol
    bajaradi — ya'ni bu funksiya ikkala holatda ham to'g'ri ishlaydi.
    """
    if day is None:
        day = schedule_services.get_slot_day(slot_id)
    if day is None:
        logger.warning("Kesh invalidatsiyasi: slot topilmadi, slot_id=%s", slot_id)
        return

    transaction.on_commit(
        lambda: schedule_cache.invalidate_doctor_day(doctor_id, day)
    )


def _specialization_label(services) -> str:
    """Metrika yorlig'i uchun mutaxassislik nomi.

    ⚠️ Prometheus yorliqlari KAM XIL qiymatli bo'lishi shart — har bir yangi
    qiymat alohida time series yaratadi. Mutaxassislik (~50 xil) mos keladi;
    bu yerga `doctor_id` yoki `booking_id` qo'yish Prometheus'ni o'ldiradi.

    Metrika hech qachon asosiy oqimni yiqitmasligi kerak, shuning uchun
    kontekst topilmasa "unknown" qaytaramiz — istisno ko'tarmaymiz.
    """
    context = catalog_services.get_service_context(services[0].id)
    return context.specialization_name if context else "unknown"


def _publish_booking_event(booking: Booking, event_type: str, new_status: str) -> None:
    """Bron hodisasini outbox'ga yozadi.

    ╔══════════════════════════════════════════════════════════════════════╗
    ║  PARTITION KALITI = booking_id.                                      ║
    ║  Kafka tartibni faqat BITTA partition ichida kafolatlaydi. Bitta      ║
    ║  bronning hodisalari (Confirmed -> keyin Cancelled) bir xil kalitga   ║
    ║  ega bo'lgani uchun bir xil partitionga tushadi va TARTIBDA o'qiladi. ║
    ║                                                                       ║
    ║  Kalit tasodifiy bo'lsa, "bekor qilindi" xabari "tasdiqlandi" dan     ║
    ║  OLDIN yetib borishi mumkin — mijoz bekor qilingan bron uchun         ║
    ║  "tasdiqlandi" SMS olardi.                                            ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    events.publish(
        topic=BOOKING_EVENTS_TOPIC,
        key=str(booking.id),
        event_type=event_type,
        payload=_build_event_payload(booking, new_status),
    )


def _build_event_payload(booking: Booking, new_status: str) -> dict:
    """Hodisa payload'i — ATAYLAB DENORMALIZATSIYA qilingan.

    Iste'molchilar bazaga qaytib murojaat qilmasligi kerak:
      * `api/notifications/consumer.py` -> booking_id (telefon bazadan), booking_number
      * `analytics/consumer.py`         -> specialization, place, city,
                                          patient_age_group, patient_gender...

    Nega hodisa o'z ichiga oladi: iste'molchi bazaga JOIN qilsa, (1) u
    boshqa servisning bazasiga bog'lanib qoladi, (2) hodisa kechikib
    o'qilganda ma'lumot ALLAQACHON O'ZGARGAN bo'lishi mumkin. Hodisa esa
    "shu lahzada shunday edi" degan o'zgarmas suratdir.

    Barcha qiymatlar JSON'ga mos turlarga o'giriladi — `payload` JSONField,
    va `Decimal`/`UUID`/`date` ni standart `json.dumps` seriyalay olmaydi.

    `new_status` ATAYLAB argument sifatida olinadi, `booking.status` dan
    emas: holat `QuerySet.update()` bilan o'zgartirilgan, ya'ni xotiradagi
    `booking` obyekti hali ESKI qiymatni saqlab turadi. Uni o'qisak,
    hodisa turi "BookingConfirmed" bo'lgani holda payload ichida
    "pending_payment" ketardi.
    """
    first_item = booking.items.first()
    context = (
        catalog_services.get_service_context(first_item.service_id)
        if first_item
        else None
    )

    place = context.place if context else "clinic"

    # ⬇ Joylashuvni SLOT belgilaydi, xizmat emas.
    # `Service.clinic` — xizmat kimga tegishli (egalik), `TimeSlot.clinic` —
    # shifokor o'sha vaqtda qayerda. Shifokor bir necha klinikada ishlashi
    # mumkin, shuning uchun bu ikkisi boshqa narsa. Shaxsiy xizmat bilan
    # klinikadagi qabulda `Service.clinic` bo'sh, lekin qabul klinikada
    # bo'ladi — shuning uchun shaharni slotdan olamiz.
    clinic_id = schedule_services.get_slot_clinic_id(booking.slot_id)

    if place == "home" and booking.address_id:
        city = booking.address.city
    elif clinic_id:
        city = catalog_services.get_clinic_city(clinic_id)
    else:
        city = context.clinic_city if context else ""

    slot = schedule_services.get_slot_for_booking(booking.slot_id)
    return {
        "booking_id": str(booking.id),
        "booking_number": booking.number,
        "created_date": booking.created_at.date().isoformat(),
        "status": new_status,
        # D9: shifokor xabari uchun. `booking.status` — xotiradagi ESKI qiymat
        # (holat `update()` bilan o'zgargan), aynan shu kerak.
        "previous_status": booking.status,
        "start_at": slot.start_at.isoformat() if slot else None,
        # Bildirishnoma uchun
        # A10: telefon hodisaga TUSHMAYDI (Kafka/ClickHouse'da abadiy qolardi).
        # SMS consumer raqamni `booking_id` bo'yicha bazadan oladi.
        "client_hash": client_hash(booking.client.phone),
        # Analitika uchun (denormalizatsiya)
        "doctor_id": str(booking.doctor_id),
        "clinic_id": str(clinic_id) if clinic_id else None,
        "specialization": context.specialization_name if context else "unknown",
        "place": place,
        "city": city or "unknown",
        "total_price": int(booking.total_price),
        "patient_age_group": _age_group(booking.patient.birth_date),
        "patient_gender": booking.patient.gender,
    }


def _age_group(birth_date) -> str:
    """Yosh guruhi — analytics/schema.py dagi qiymatlar bilan bir xil.

    Aniq yosh emas, GURUH yuboriladi: analitikaga guruh yetarli, va
    shaxsni aniqlash imkoniyatini kamaytiradi (ma'lumotni minimallashtirish).
    """
    if birth_date is None:
        return "unknown"

    today = timezone.now().date()
    years = (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )

    if years < 18:
        return "0-17"
    if years <= 40:
        return "18-40"
    if years <= 65:
        return "41-65"
    return "65+"


MAX_PENDING_BOOKINGS_PER_CLIENT = 3


def _validate_and_load_services(request: BookingRequest):
    """Barcha biznes tekshiruvlari BITTA joyda.

    Tekshiruvni view yoki serializer ichiga tarqatib yubormang — keyin
    servisga ajratganda ularni yig'ib olish og'ir bo'ladi.

    QOIDA (A4): so'rovdan kelgan har bir ID faqat egalik/moslik tekshiruvchi
    loader orqali o'qiladi. "Topilmadi" va "begona" uchun xato matni BIR XIL —
    javobdan boshqa odamning obyekti mavjudligini bilib bo'lmaydi.
    """
    if not request.service_ids:
        raise InvalidBookingRequest("Kamida bitta xizmat tanlanishi kerak")
    if len(set(request.service_ids)) != len(request.service_ids):
        raise InvalidBookingRequest("Xizmatlar takrorlanmasligi kerak")

    if not catalog_services.is_doctor_bookable(request.doctor_id):
        raise InvalidBookingRequest("Shifokor hozircha bron qabul qilmaydi")

    # A1 — bemor va manzil so'rovchiniki bo'lishi shart
    patient = patient_services.get_owned_patient(request.client_id, request.patient_id)
    if patient is None:
        raise InvalidBookingRequest("patient_id noto'g'ri")

    # A2 — slot aynan shu shifokorniki
    slot = schedule_services.get_slot_for_booking(request.slot_id)
    if slot is None or slot.doctor_id != request.doctor_id:
        raise InvalidBookingRequest("slot_id noto'g'ri")

    # A3 — xizmat shu shifokorniki yoki u ishlaydigan klinikaniki
    services = catalog_services.get_bookable_services(
        request.doctor_id, request.service_ids
    )
    if len(services) != len(request.service_ids):
        raise InvalidBookingRequest("Ba'zi xizmatlar topilmadi yoki faol emas")

    places = {s.place for s in services}
    if len(places) > 1:
        raise InvalidBookingRequest(
            "Bitta bronda klinika va uy xizmatlarini aralashtirib bo'lmaydi"
        )

    # A3 — joy: uy xizmati faqat uy slotiga, klinika xizmati faqat klinika slotiga
    is_home_visit = places.pop() == "home"
    if is_home_visit != slot.is_home_visit:
        raise InvalidBookingRequest("Tanlangan xizmat bu vaqtdagi qabul joyiga mos emas")

    buffer_minutes = None
    if is_home_visit:
        if request.address_id is None:
            raise InvalidBookingRequest("Uy chaqiruvi uchun manzil ko'rsatilishi shart")
        address = patient_services.get_owned_address(request.client_id, request.address_id)
        if address is None:
            raise InvalidBookingRequest("address_id noto'g'ri")
        buffer_minutes = _check_home_visit_reach(request.doctor_id, address)
    elif request.address_id is not None:
        if patient_services.get_owned_address(request.client_id, request.address_id) is None:
            raise InvalidBookingRequest("address_id noto'g'ri")

    # Klinika xizmati boshqa klinikadagi slotga tushmasin
    for s in services:
        if s.doctor_id is None and s.clinic_id != slot.clinic_id:
            raise InvalidBookingRequest("Tanlangan xizmat bu vaqtdagi qabul joyiga mos emas")

    # C6 — bemor (yosh, jins) mutaxassislikka mos bo'lishi shart
    check_patient_fits(patient, services, at=slot.start_at)

    # A3/C5 — xizmatlar slotga sig'ishi shart (variant A)
    total_duration = sum(s.duration_minutes for s in services)
    if total_duration > slot.duration_minutes:
        raise InvalidBookingRequest(
            f"Bu xizmatlar uchun {total_duration} daqiqa kerak, "
            f"tanlangan vaqt {slot.duration_minutes} daqiqa"
        )

    # A7/C7 — biznes cheklovlari (409: so'rov to'g'ri, lekin hozir bajarib bo'lmaydi)
    # Hold muddati o'tgan pending bron (cron hali yopmagan) hisobga olinmaydi —
    # lazy expiry bilan u allaqachon "yo'q" hisoblanadi.
    live = Q(status=Booking.Status.CONFIRMED) | Q(
        status=Booking.Status.PENDING_PAYMENT, slot__hold_expires_at__gte=timezone.now()
    )
    pending = Booking.objects.filter(
        live, client_id=request.client_id, status=Booking.Status.PENDING_PAYMENT
    ).count()
    if pending >= MAX_PENDING_BOOKINGS_PER_CLIENT:
        raise BookingError("Avval oldingi bronlarni to'lang yoki bekor qiling")

    overlapping = Booking.objects.filter(
        live,
        patient_id=request.patient_id,
        slot__start_at__lt=slot.end_at,
        slot__end_at__gt=slot.start_at,
    ).exists()
    if overlapping:
        raise BookingError("Bu bemorda shu vaqtda boshqa qabul bor")

    return services, buffer_minutes


def _check_home_visit_reach(doctor_id: UUID, address) -> int:
    """C4: manzil shifokor radiusi ichidami. Qaytaradi — dinamik yo'l buferi (daqiqa).

    Shifokor bazasi kiritilmagan bo'lsa radius tekshirilmaydi va default
    bufer ishlatiladi (eski profillar bron qabul qilishda davom etadi)."""
    area = catalog_services.get_home_visit_area(doctor_id)
    distance = area.distance_km(address.latitude, address.longitude) if area else None
    if distance is not None and distance > area.radius_km:
        raise InvalidBookingRequest("Shifokor bu manzilga bormaydi")
    return schedule_services.travel_buffer_minutes(distance)


def _travel_buffer_for_booking(booking: Booking) -> int:
    """Ko'chirishda yangi slot uchun bufer — o'sha manzil, o'sha masofa."""
    if booking.address_id is None:
        return schedule_services.HOME_VISIT_BUFFER_MINUTES
    address = patient_services.get_owned_address(booking.client_id, booking.address_id)
    area = catalog_services.get_home_visit_area(booking.doctor_id)
    distance = area.distance_km(address.latitude, address.longitude) if (area and address) else None
    return schedule_services.travel_buffer_minutes(distance)


ADULT_AGE = 18


def age_on(birth_date, when) -> int:
    """To'liq yillar soni `when` sanasida (qabul kuni, bugun emas —
    bir oydan keyingi qabulgacha bola 18 ga to'lishi mumkin)."""
    day = when.date() if hasattr(when, "date") else when
    return day.year - birth_date.year - ((day.month, day.day) < (birth_date.month, birth_date.day))


def check_patient_fits(patient, services, *, at) -> None:
    """C6: bemor xizmatlarning mutaxassisligiga mosmi. Sof qoida — DTO'lar bilan ishlaydi."""
    age = age_on(patient.birth_date, timezone.localtime(at, schedule_services.LOCAL_TZ))
    rules = catalog_services.get_specialization_rules(s.specialization_id for s in services)
    for spec in rules:
        if spec.is_pediatric and age >= ADULT_AGE:
            raise InvalidBookingRequest(f"{spec.name}: bu shifokor faqat bolalar bilan ishlaydi")
        if not spec.is_pediatric and age < ADULT_AGE and not spec.accepts_children:
            raise InvalidBookingRequest(f"{spec.name}: bu shifokor bolalarni qabul qilmaydi")
        if spec.min_patient_age is not None and age < spec.min_patient_age:
            raise InvalidBookingRequest(f"{spec.name}: bemor kamida {spec.min_patient_age} yoshda bo'lishi kerak")
        if spec.max_patient_age is not None and age > spec.max_patient_age:
            raise InvalidBookingRequest(f"{spec.name}: bemor ko'pi bilan {spec.max_patient_age} yoshda bo'lishi kerak")
        if spec.allowed_gender and patient.gender != spec.allowed_gender:
            raise InvalidBookingRequest(f"{spec.name}: bu mutaxassislik bemor jinsiga mos emas")


def _expire_stale_booking_on_slot(slot_id: UUID) -> None:
    """Lazy expiry (E8) natijasi: muddati o'tgan hold'ni yangi mijoz oldi.

    Eski `pending_payment` bron hali `expire_bookings` tomonidan yopilmagan
    bo'lishi mumkin — uni shu tranzaksiyada EXPIRED qilamiz. Slotni
    bo'shatmaymiz: u allaqachon yangi hold ostida.
    """
    for stale in Booking.objects.filter(
        slot_id=slot_id, status=Booking.Status.PENDING_PAYMENT
    ):
        Booking.objects.filter(id=stale.id).update(
            status=Booking.Status.EXPIRED,
            cancelled_reason="To'lov muddati o'tdi",
            updated_at=timezone.now(),
        )
        _publish_booking_event(stale, "BookingExpired", Booking.Status.EXPIRED)
        logger.info("Muddati o'tgan hold qayta sotildi: %s", stale.number)


def _generate_number(attempts: int = 5) -> str:
    """Mijozga ko'rsatiladigan bron raqami: MB-260729-K4T9

    UUID'ni mijozga ko'rsatmang — telefonda aytib bo'lmaydi.
    Chalkashadigan belgilar (0/O, 1/I) alifbodan chiqarilgan.

    Sana MAHALLIY vaqtda (C8): UTC'da Toshkentning 00:00–05:00 oralig'idagi
    bronlari kechagi sana bilan raqamlanardi.
    """
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    today = timezone.localtime(timezone.now(), schedule_services.LOCAL_TZ).strftime("%y%m%d")

    for _ in range(attempts):
        suffix = "".join(secrets.choice(alphabet) for _ in range(4))
        number = f"MB-{today}-{suffix}"
        if not Booking.objects.filter(number=number).exists():
            return number

    raise BookingError("Bron raqami generatsiya qilinmadi")


def _get_booking(booking_id: UUID) -> Booking:
    booking = Booking.objects.filter(id=booking_id).first()
    if booking is None:
        raise BookingError(f"Bron topilmadi: {booking_id}")
    return booking


# ---------------------------------------------------------------------------
# FAZA 4 DA NIMA O'ZGARADI — hozirdan o'qib qo'ying
# ---------------------------------------------------------------------------
#
# 1. `transaction.atomic()` yo'qoladi.
#    Jadval boshqa servisda, boshqa bazada — bitta tranzaksiyaga sig'maydi.
#
# 2. `hold_slot` gRPC chaqiruviga aylanadi.
#    Timeout, retry va circuit breaker qo'shiladi. Eng muhimi: javob
#    kelmasa slot band bo'ldimi yoki yo'qmi — BILMAYSIZ. Shuning uchun
#    har bir chaqiruvga `idempotency_key` beriladi va qayta so'rasangiz
#    o'sha natijani qaytaradi.
#
# 3. `create_booking` saga bo'ladi:
#       hold_slot -> create_payment -> [kutish] -> confirm | cancel
#    Har qadam natijasi `SagaState` jadvaliga yoziladi, chunki jarayon
#    o'rtasida servis o'lishi mumkin va qayta ko'tarilganda qayerdan
#    davom etishni bilishi kerak.
#
# 4. Kompensatsiya zanjiri paydo bo'ladi:
#       to'lov yiqildi -> release_slot -> booking CANCELLED -> mijozga SMS
#
# 5. Oraliq holat ko'rinadigan bo'ladi.
#    Bir necha soniya davomida "pul yechilgan, lekin bron tasdiqlanmagan"
#    holati mavjud bo'ladi. Buni interfeys darajasida hal qilish kerak —
#    mijozga "to'lov tekshirilmoqda" deb ko'rsatiladi.
#    Bu — eventual consistency'ning biznes narxi.