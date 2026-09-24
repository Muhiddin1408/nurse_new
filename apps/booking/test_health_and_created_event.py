"""`BookingCreated` hodisasi va readiness Kafka tekshiruvi uchun testlar."""
import json

import pytest

from apps.utils.models import OutboxEvent


# ---------------------------------------------------------------------------
# BookingCreated — voronka maxraji
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_booking_created_hodisasini_chiqaradi(client_user, patient, doctor, clinic, service, db):
    from api.booking import services as bs

    from apps.schedule.models import TimeSlot
    from datetime import timedelta
    from django.utils import timezone

    start = timezone.now() + timedelta(hours=5)
    slot = TimeSlot.objects.create(
        # klinika xizmati -> klinika sloti (A3: joy mos bo'lishi shart)
        doctor_id=doctor.id, clinic_id=clinic.id,
        start_at=start, end_at=start + timedelta(minutes=30),
        status=TimeSlot.Status.FREE,
    )

    booking = bs.create_booking(
        bs.BookingRequest(
            client_id=client_user.id,
            patient_id=patient.id,
            doctor_id=doctor.id,
            slot_id=slot.id,
            service_ids=[service.id],
        )
    )

    ev = OutboxEvent.objects.get()
    assert ev.event_type == "BookingCreated"
    assert ev.key == str(booking.id)          # partition kaliti
    assert ev.payload["status"] == "pending_payment"
    json.dumps(ev.payload)                    # seriyalanadimi

    # Analitika uni voronka maxraji sifatida qabul qiladimi
    from analytics.consumer import _to_fact_row

    row = _to_fact_row({"event_type": ev.event_type, "data": ev.payload})
    assert row["status"] == "pending_payment"
    # Yaratilgan bron bekor qilingan HISOBLANMAYDI
    assert row["is_cancelled"] == 0


@pytest.mark.django_db
def test_voronka_tola_ketma_ketlik(pending_booking):
    """created -> confirmed: ikkala hodisa ham, tartibda, bir xil kalit bilan."""
    from api.booking import services as bs

    # pending_booking fixture'i create_booking'dan o'tmagan, shuning uchun
    # bu yerda faqat confirm zanjirini tekshiramiz
    bs.confirm_booking(pending_booking.id)
    bs.cancel_booking(pending_booking.id)

    evs = list(OutboxEvent.objects.order_by("created_at"))
    assert [e.event_type for e in evs] == ["BookingConfirmed", "BookingCancelled"]
    assert len({e.key for e in evs}) == 1


def test_created_hodisasi_sms_chiqarmaydi():
    """Mijoz hali to'lov oynasida — unga SMS yuborilmaydi."""
    from api.notifications.consumer import IGNORED_EVENTS

    assert "BookingCreated" in IGNORED_EVENTS


def test_analitika_soralari_holat_boyicha_filtrlaydi():
    """`pending_payment` qatorlari ko'rsatkichlarni buzmasligi kerak."""
    from analytics.queries import QUERIES

    # Bekor qilish darajasi faqat yakuniy holatlardan hisoblanadi
    assert "status IN ('confirmed', 'cancelled', 'expired')" in QUERIES[
        "cancellation_rate_by_specialization"
    ]
    # Uy/klinika taqsimoti bitta bronni uch marta sanamaydi
    assert "status = 'confirmed'" in QUERIES["home_vs_clinic_by_city"]
    # Voronkada maxraj bor
    assert "pending_payment" in QUERIES["conversion_funnel"]


# ---------------------------------------------------------------------------
# readiness — Kafka endi haqiqatan tekshiriladi
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_readiness_kafka_olganda_ham_200_qaytaradi(client, monkeypatch):
    """Kafka o'lgani pod'ni trafikdan chiqarmaydi (outbox arxitekturasi)."""
    from api.observability import health

    def olgan_kafka():
        raise RuntimeError("ulanib bo'lmadi")

    monkeypatch.setattr(health, "_check_kafka", olgan_kafka)

    r = client.get("/healthz/ready")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    # ⬇ ASOSIY: nosozlik YASHIRILMAYDI, javobda ko'rinadi
    assert body["checks"]["kafka"].startswith("degraded:")


@pytest.mark.django_db
def test_readiness_kafka_sogolom_bolsa_ok(client, monkeypatch):
    from api.observability import health

    monkeypatch.setattr(health, "_check_kafka", lambda: None)

    r = client.get("/healthz/ready")
    assert r.status_code == 200
    assert r.json()["checks"]["kafka"] == "ok"


@pytest.mark.django_db
def test_readiness_baza_olganda_503(client, monkeypatch):
    """Baza — KRITIK bog'liqlik, u yiqilsa pod trafik qabul qilmasligi kerak."""
    from api.observability import health

    class OlganCursor:
        def __enter__(self):
            raise RuntimeError("baza yo'q")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(health.connection, "cursor", lambda: OlganCursor())
    monkeypatch.setattr(health, "_check_kafka", lambda: None)

    r = client.get("/healthz/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"


def test_check_kafka_endi_pass_emas():
    """Bo'sh tana qaytib kelmasligini qo'riqlaydi — u "yolg'on ok" berardi."""
    import inspect

    from api.observability.health import _check_kafka

    manba = inspect.getsource(_check_kafka)
    assert "list_topics" in manba
    assert "timeout" in manba
