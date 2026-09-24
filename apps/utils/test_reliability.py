"""Sprint 1.4 — ishonchlilik qatlami: E1, E2, E3, E4, E5, E13."""

import json
import logging
from datetime import timedelta
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from api.events import publisher as pub
from api.events import services as events
from apps.utils.models import DeadLetterEvent, OutboxEvent


class FakeProducer:
    """Delivery natijasini boshqaradigan soxta Kafka producer."""

    def __init__(self, error=None):
        self.error = error
        self.sent = []
        self._callbacks = []

    def produce(self, topic, key, value, on_delivery):
        self.sent.append((topic, key, json.loads(value)))
        self._callbacks.append(on_delivery)

    def poll(self, timeout):
        return 0

    def flush(self, timeout=None):
        for cb in self._callbacks:
            cb(self.error, None)
        self._callbacks = []
        return 0


def make_event(**kw):
    defaults = dict(topic="booking.events", key="b-1", event_type="BookingConfirmed", payload={"x": 1})
    defaults.update(kw)
    return OutboxEvent.objects.create(**defaults)


# --- E1 / E3 publisher ------------------------------------------------------


@pytest.mark.django_db
def test_publisher_yetkazadi_va_konvertda_correlation_id():
    ev = make_event(correlation_id="req-123")
    producer = FakeProducer()
    assert pub.OutboxPublisher(producer=producer)._process_batch() == 1

    ev.refresh_from_db()
    assert ev.status == OutboxEvent.Status.PUBLISHED
    _, key, envelope = producer.sent[0]
    assert key == b"b-1"
    assert envelope["correlation_id"] == "req-123"
    assert envelope["event_id"] == str(ev.id)


@pytest.mark.django_db
def test_xato_bolsa_backoff_va_muddatgacha_qayta_olinmaydi():
    ev = make_event()
    publisher = pub.OutboxPublisher(producer=FakeProducer(error="broker down"))
    publisher._process_batch()

    ev.refresh_from_db()
    assert ev.status == OutboxEvent.Status.PENDING
    assert ev.attempts == 1
    assert ev.next_retry_at > timezone.now()
    assert "broker down" in ev.last_error
    # backoff muddati o'tmaguncha partiyaga tushmaydi — Kafka'ga DDoS yo'q
    assert publisher._process_batch() == 0


def test_backoff_eksponensial_va_cheklangan():
    assert pub.backoff_delay(1).total_seconds() < 3
    assert 8 <= pub.backoff_delay(3).total_seconds() < 9
    assert pub.backoff_delay(20).total_seconds() < pub.MAX_BACKOFF_SECONDS + 1


@pytest.mark.django_db
def test_max_urinishdan_keyin_failed_va_republish():
    ev = make_event(attempts=pub.MAX_ATTEMPTS - 1)
    pub.OutboxPublisher(producer=FakeProducer(error="x"))._process_batch()
    ev.refresh_from_db()
    assert ev.status == OutboxEvent.Status.FAILED

    call_command("republish_outbox", "--event-id", str(ev.id), "--dry-run")
    ev.refresh_from_db()
    assert ev.status == OutboxEvent.Status.FAILED

    call_command("republish_outbox", "--since", (timezone.now() - timedelta(hours=1)).isoformat(),
                 "--type", "BookingConfirmed")
    ev.refresh_from_db()
    assert (ev.status, ev.attempts) == (OutboxEvent.Status.PENDING, 0)

    pub.OutboxPublisher(producer=FakeProducer())._process_batch()
    ev.refresh_from_db()
    assert ev.status == OutboxEvent.Status.PUBLISHED


# --- E2 metrikalar ----------------------------------------------------------


@pytest.mark.django_db
def test_metrics_endpoint_outbox_lag_korsatadi():
    ev = make_event()
    OutboxEvent.objects.filter(id=ev.id).update(created_at=timezone.now() - timedelta(minutes=10))
    resp = APIClient().get("/healthz/metrics")
    assert resp.status_code == 200
    body = resp.content.decode()
    lag_line = next(l for l in body.splitlines() if l.startswith("medbron_outbox_lag_seconds "))
    assert float(lag_line.split()[1]) >= 599
    assert "medbron_outbox_pending_total 1.0" in body
    assert "medbron_bookings_created_total" in body or "medbron_booking_duration_seconds" in body


# --- E5 DLQ + tranzaksion idempotentlik -------------------------------------


POISON_ID = "7d3a0b8e-0000-4000-8000-00000000dead"


class Msg:
    def __init__(self, value, offset=0):
        self._v = value if isinstance(value, bytes) else json.dumps(value).encode()
        self._o = offset

    def value(self):
        return self._v

    def error(self):
        return None

    def topic(self):
        return "booking.events"

    def partition(self):
        return 0

    def offset(self):
        return self._o


@pytest.mark.django_db
def test_notification_buzuq_payload_dlq_ga():
    from api.notifications import consumer

    env = {"event_id": POISON_ID, "event_type": "BookingConfirmed", "data": {"booking_number": "MB-1"}}
    with mock.patch("api.notifications.services.send_sms") as send:
        consumer._process_message(Msg(env))
    send.assert_not_called()
    dl = DeadLetterEvent.objects.get()
    assert (dl.consumer, dl.event_id) == ("notification-service", POISON_ID)
    assert "client_phone" in dl.error


@pytest.mark.django_db
def test_uuid_bolmagan_event_id_consumerni_yiqitmaydi():
    from api.notifications import consumer

    env = {"event_id": "uuid-emas", "event_type": "BookingConfirmed",
           "data": {"client_phone": "+998901112233", "booking_number": "MB-1"}}
    with mock.patch("api.notifications.services.send_sms"):
        consumer._process_message(Msg(env))
    assert DeadLetterEvent.objects.get().event_id == "uuid-emas"


@pytest.mark.django_db
def test_sms_vaqtinchalik_xato_bolsa_hodisa_ishlangan_deb_belgilanmaydi():
    from api.notifications import consumer
    from apps.notifications.models import ProcessedEvent

    env = {"event_id": "e2e62c7e-cb00-4730-a7d7-dcd15e86375b", "event_type": "BookingConfirmed",
           "data": {"client_phone": "+998901112233", "booking_number": "MB-1"}}
    with mock.patch("api.notifications.services.send_sms", side_effect=ConnectionError("provayder")):
        with pytest.raises(ConnectionError):
            consumer._process_message(Msg(env))
    assert not ProcessedEvent.objects.filter(event_id="e2e62c7e-cb00-4730-a7d7-dcd15e86375b").exists()

    with mock.patch("api.notifications.services.send_sms") as send:
        consumer._process_message(Msg(env))  # qayta o'qildi — endi SMS ketadi
    send.assert_called_once()


@pytest.mark.django_db
def test_analytics_buzuq_qator_dlq_va_partition_bloklanmaydi():
    from analytics import consumer as ac
    from api.events.runner import GracefulStop

    good = {"event_id": "g", "event_type": "BookingConfirmed",
            "data": {"booking_id": "b", "created_date": "2026-09-15", "doctor_id": "d"}}
    bad = {"event_id": "p", "event_type": "BookingConfirmed",
           "data": {"booking_id": "b", "created_date": "notadate", "doctor_id": "d"}}

    stop = GracefulStop(install_signals=False)

    class C:
        def __init__(self):
            self.msgs = [Msg(bad, 0), Msg(good, 1)]
            self.commits = 0

        def subscribe(self, t):
            pass

        def poll(self, timeout):
            if self.msgs:
                return self.msgs.pop(0)
            stop.set()
            return None

        def commit(self, asynchronous=True):
            self.commits += 1

        def close(self):
            pass

    written = []
    with mock.patch.object(ac, "_flush", side_effect=lambda rows: written.extend(rows)):
        ac.run_analytics_consumer(stop=stop, consumer=C())
    assert [r["event_id"] for r in written] == ["g"]
    assert DeadLetterEvent.objects.get().consumer == "analytics-loader"


def test_clickhouse_sxemasi_replacing_merge_tree_va_final():
    from analytics.queries import AGG_QUERIES, QUERIES
    from analytics.schema import SCHEMA

    fact = SCHEMA.split("CREATE TABLE IF NOT EXISTS fact_booking")[1].split("CREATE TABLE")[0]
    assert "ReplacingMergeTree" in fact and "event_id" in fact
    assert all("FROM fact_booking FINAL" in q for q in QUERIES.values())
    # E14: agregatda ham FINAL shart — qayta hisoblash eski qatorni fonda
    # almashtiradi, usiz bitta kun ikki marta sanalishi mumkin
    assert all("FROM daily_booking_agg FINAL" in q for q in AGG_QUERIES.values())


# --- E13 correlation_id -----------------------------------------------------


@pytest.mark.django_db
def test_correlation_id_sorovdan_hodisaga_va_xato_javobiga(pending_booking):
    api = APIClient()
    api.force_authenticate(pending_booking.client)

    resp = api.post(f"/api/v1/booking/bookings/{pending_booking.id}/cancel", {}, format="json",
                    HTTP_X_CORRELATION_ID="abc-123")
    assert resp["X-Correlation-ID"] == "abc-123"
    assert OutboxEvent.objects.get(event_type="BookingCancelled").correlation_id == "abc-123"

    # xato javobida trace_id
    resp = api.post(f"/api/v1/booking/bookings/{pending_booking.id}/cancel", {"reason": "x" * 600},
                    format="json", HTTP_X_CORRELATION_ID="err-9")
    assert resp.status_code == 400
    assert resp.json()["trace_id"] == "err-9"


def test_notogri_correlation_header_almashtiriladi():
    from api.observability.correlation import correlation_scope

    with correlation_scope("<script>") as cid:
        assert cid != "<script>" and len(cid) == 32


def test_json_log_formati():
    from api.observability.correlation import CorrelationIdFilter, JsonFormatter, correlation_scope

    record = logging.LogRecord("x", logging.INFO, __file__, 1, "salom %s", ("dunyo",), None)
    with correlation_scope("cid-1"):
        CorrelationIdFilter().filter(record)
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "salom dunyo"
    assert data["correlation_id"] == "cid-1"
    assert data["level"] == "INFO"


@pytest.mark.django_db
def test_consumer_konvertdagi_correlation_id_ni_logga_olib_kiradi(caplog):
    from api.notifications import consumer

    env = {"event_id": "12aaa1a2-a7b6-4bd3-9068-1329c27d6b29", "event_type": "BookingConfirmed", "correlation_id": "trace-77",
           "data": {"client_phone": "+998901112233", "booking_number": "MB-1"}}
    seen = {}

    def capture(**kw):
        from api.observability.correlation import get_correlation_id
        seen["cid"] = get_correlation_id()

    with mock.patch("api.notifications.services.send_sms", side_effect=capture):
        consumer._process_message(Msg(env))
    assert seen["cid"] == "trace-77"
