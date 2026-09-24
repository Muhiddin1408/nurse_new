import json
import pytest
from apps.utils.models import OutboxEvent


@pytest.mark.django_db
def test_confirm_hodisa_outboxga_tushadi(pending_booking):
    from api.booking import services as bs
    bs.confirm_booking(pending_booking.id)

    ev = OutboxEvent.objects.get()
    assert ev.topic == "booking.events"
    assert ev.event_type == "BookingConfirmed"
    assert ev.key == str(pending_booking.id)          # partition kaliti
    assert ev.status == OutboxEvent.Status.PENDING
    print("\npayload:", json.dumps(ev.payload, indent=2, ensure_ascii=False))

    # JSON'ga seriyalanadimi (Decimal/UUID/date qolmaganini isbotlaydi)
    json.dumps(ev.payload)

    # notifications consumer talab qiladigan kalitlar
    # A10: telefon hodisada yo'q — faqat HMAC
    assert "client_phone" not in ev.payload
    assert len(ev.payload["client_hash"]) == 32
    assert pending_booking.client.phone not in json.dumps(ev.payload)
    assert ev.payload["booking_number"]

    # analytics consumer aynan shu payload'dan qator qura oladimi
    from analytics.consumer import _to_fact_row
    row = _to_fact_row({"event_type": "BookingConfirmed", "data": ev.payload})
    print("fact_booking qatori:", json.dumps(row, indent=2, ensure_ascii=False, default=str))
    assert row["created_date"].isoformat() == ev.payload["created_date"]  # ClickHouse Date uchun `date`
    assert row["specialization"] == "Terapevt"
    assert row["place"] == "clinic"
    assert row["city"] == "Toshkent"
    assert row["total_price"] == 150000
    assert row["is_cancelled"] == 0


@pytest.mark.django_db
def test_cancel_hodisa_va_tartib(pending_booking):
    from api.booking import services as bs
    bs.confirm_booking(pending_booking.id)
    bs.cancel_booking(pending_booking.id, reason="mijoz bekor qildi")

    evs = list(OutboxEvent.objects.order_by("created_at"))
    assert [e.event_type for e in evs] == ["BookingConfirmed", "BookingCancelled"]
    # bir xil kalit -> bir xil partition -> TARTIB kafolatlanadi
    assert len({e.key for e in evs}) == 1


@pytest.mark.django_db
def test_idempotent_qayta_tasdiqlash_ikkinchi_hodisa_yozmaydi(pending_booking):
    from api.booking import services as bs
    bs.confirm_booking(pending_booking.id)
    bs.confirm_booking(pending_booking.id)   # takror
    assert OutboxEvent.objects.count() == 1


@pytest.mark.django_db
def test_tolov_bekor_qilinsa_kompensatsiya_hodisa_chiqaradi(pending_booking):
    """Bajarilgan to'lov qaytarilsa (state -2) bron bekor bo'ladi va hodisa chiqadi."""
    from django.utils import timezone

    from api.payments.webhook import handle_payme_webhook

    handle_payme_webhook(method="CreateTransaction", params={
        "id": "tx-1", "time": int(timezone.now().timestamp() * 1000),
        "amount": int(pending_booking.total_price * 100),
        "account": {"booking_id": str(pending_booking.id)},
    })
    handle_payme_webhook(method="PerformTransaction", params={"id": "tx-1"})
    OutboxEvent.objects.all().delete()

    handle_payme_webhook(method="CancelTransaction", params={"id": "tx-1", "reason": 5})
    ev = OutboxEvent.objects.get(topic="booking.events")
    assert ev.event_type == "BookingCancelled"
    # pul harakati ham hodisa sifatida chiqadi (ledger + SMS uchun)
    assert set(OutboxEvent.objects.filter(topic="payment.events").values_list("event_type", flat=True)) == {
        "RefundRequested", "RefundSucceeded"
    }


@pytest.mark.django_db
def test_muddati_otgan_bron_expired_hodisa_chiqaradi(pending_booking):
    """Muddati o'tgan bron `BookingExpired` chiqaradi, `BookingCancelled` EMAS.

    Ilgari `expire_pending_bookings` `cancel_booking` ni chaqirib, keyin
    alohida `UPDATE` bilan `EXPIRED` qo'yardi — natijada baza `expired`,
    hodisa esa `cancelled` bo'lib, ikkalasi bir-biriga zid edi.

    Bu test aynan shu zidlikning qaytib kelmasligini qo'riqlaydi.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.booking.models import Booking
    from apps.schedule.models import TimeSlot
    from api.booking import services as bs

    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )
    assert bs.expire_pending_bookings() == 1

    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.EXPIRED

    ev = OutboxEvent.objects.get()
    assert ev.event_type == "BookingExpired"
    # Holat va hodisa endi bir xil narsani aytadi
    assert ev.payload["status"] == "expired"

    # Slot bo'shatildi — boshqa mijoz band qila oladi
    slot = TimeSlot.objects.get(id=pending_booking.slot_id)
    assert slot.status == TimeSlot.Status.FREE


@pytest.mark.django_db
def test_expired_bron_bekor_qilish_darajasiga_qoshilmaydi(pending_booking):
    """`is_cancelled = 0` — bu tuzatishning ASOSIY maqsadi.

    Muddat o'tishi "mijoz to'lay olmadi", bekor qilish esa "mijoz fikridan
    qaytdi". Ikkalasi bitta ustunga yig'ilsa, to'lov integratsiyasidagi
    muammo mijoz xatti-harakati bo'lib ko'rinadi.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.schedule.models import TimeSlot
    from api.booking import services as bs
    from analytics.consumer import _to_fact_row

    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )
    bs.expire_pending_bookings()
    ev = OutboxEvent.objects.get()

    row = _to_fact_row({"event_type": ev.event_type, "data": ev.payload})

    # conversion_funnel dagi countIf(status = 'expired') endi ishlaydi
    assert row["status"] == "expired"
    # cancellation_rate_by_specialization sun'iy oshmaydi
    assert row["is_cancelled"] == 0


@pytest.mark.django_db
def test_expired_bron_boshqa_sms_matnini_oladi(pending_booking):
    """Mijozga "bekor qilindi" emas, "to'lov kelmadi, qayta urinib ko'ring"."""
    from api.notifications.templates import render_template

    cancelled = render_template("booking_cancelled", "uz", {"number": "MB-1"})
    expired = render_template("booking_expired", "uz", {"number": "MB-1"})

    assert cancelled != expired
    assert "qayta bron" in expired.lower()


@pytest.mark.django_db
def test_expire_idempotent_ikkinchi_hodisa_yozmaydi(pending_booking):
    """Job ikki marta ishga tushsa ham bitta hodisa — Kafka uchun shart."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.schedule.models import TimeSlot
    from api.booking import services as bs

    TimeSlot.objects.filter(id=pending_booking.slot_id).update(
        hold_expires_at=timezone.now() - timedelta(minutes=1)
    )
    bs.expire_pending_bookings()
    bs.expire_booking(pending_booking.id)  # takror

    assert OutboxEvent.objects.count() == 1
