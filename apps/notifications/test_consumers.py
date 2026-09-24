"""Kafka consumer sikllari — soxta consumer bilan (Kafka kerak emas)."""

import json
from unittest import mock

import pytest
from django.core.management import get_commands

from api.events.runner import GracefulStop


class FakeMsg:
    def __init__(self, value, offset=0):
        self._value = value if isinstance(value, bytes) else json.dumps(value).encode()
        self._offset = offset

    def value(self):
        return self._value

    def error(self):
        return None

    def topic(self):
        return "booking.events"

    def partition(self):
        return 0

    def offset(self):
        return self._offset


class FakeConsumer:
    """Xabarlar tugagach `stop` ni yoqadi — sikl o'z-o'zidan tugaydi."""

    def __init__(self, messages, stop):
        self.messages = list(messages)
        self.stop = stop
        self.commits = []
        self.closed = False

    def subscribe(self, topics):
        pass

    def poll(self, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        self.stop.set()
        return None

    def commit(self, msg=None, asynchronous=True):
        self.commits.append(msg)

    def close(self):
        self.closed = True


EVENT_ID = "3f1c2a9e-0000-4000-8000-0000000000e1"


def _envelope(event_id, event_type="BookingConfirmed", **data):
    base = {
        "client_phone": "+998901234567",
        "booking_number": "MB-1",
        "booking_id": "8b0e7b0e-0000-4000-8000-000000000001",
        "doctor_id": "8b0e7b0e-0000-4000-8000-000000000002",
        "created_date": "2026-09-14",
    }
    base.update(data)
    return {"event_id": event_id, "event_type": event_type, "data": base}


def test_barcha_worker_buyruqlari_royxatda():
    commands = get_commands()
    for name in (
        "run_outbox_publisher",
        "run_notification_consumer",
        "run_search_projector",
        "run_analytics_consumer",
    ):
        assert name in commands


@pytest.mark.django_db(transaction=True)
def test_notification_buzuq_xabar_consumerni_toxtatmaydi_va_takror_sms_yoq():
    from api.notifications.consumer import run_consumer

    stop = GracefulStop(install_signals=False)
    consumer = FakeConsumer(
        [
            FakeMsg(b"bu json emas", 0),
            FakeMsg(_envelope(EVENT_ID), 1),
            FakeMsg(_envelope(EVENT_ID), 2),  # Kafka takrori
        ],
        stop,
    )

    with mock.patch("api.notifications.services.send_sms") as send_sms:
        run_consumer(stop=stop, consumer=consumer)

    assert send_sms.call_count == 1
    assert len(consumer.commits) == 3  # buzuq xabar ham commit — navbat tiqilmaydi
    assert consumer.closed


@pytest.mark.django_db
def test_projector_upsert_va_buzuq_xabar():
    from api.search.projector import run_projector
    from apps.utils.models import DeadLetterEvent

    stop = GracefulStop(install_signals=False)
    es = mock.Mock()
    es.indices.exists.return_value = True
    consumer = FakeConsumer(
        [
            FakeMsg({"no": "envelope"}, 0),
            FakeMsg({"event_id": "e", "event_type": "DoctorApproved",
                     "data": {"id": "d-1", "full_name": "Dr"}}, 1),
        ],
        stop,
    )

    run_projector(stop=stop, consumer=consumer, es=es)

    es.index.assert_called_once()
    assert es.index.call_args.kwargs["id"] == "d-1"
    assert len(consumer.commits) == 2
    # E5: buzuq xabar jim yo'qolmaydi — DLQ da qoladi
    assert DeadLetterEvent.objects.get().consumer == "search-projector"


def test_analytics_toxtaganda_buferni_yozadi_va_commit_qiladi():
    from analytics import consumer as analytics_consumer

    stop = GracefulStop(install_signals=False)
    consumer = FakeConsumer(
        [FakeMsg(_envelope("e-1", "BookingCreated")), FakeMsg(_envelope("e-2", "Noma'lum"))],
        stop,
    )
    client = mock.Mock()

    with mock.patch.object(analytics_consumer, "_clickhouse_client", return_value=client):
        analytics_consumer.run_analytics_consumer(stop=stop, consumer=consumer)

    client.insert.assert_called_once()
    table, rows = client.insert.call_args.args
    columns = client.insert.call_args.kwargs["column_names"]
    assert table == "fact_booking"
    assert len(rows) == 1 and isinstance(rows[0], list)
    assert rows[0][columns.index("status")] == "pending_payment"
    assert consumer.commits  # to'xtashda commit — qayta ishga tushganda dublikat yo'q
    assert consumer.closed


def test_analytics_sxema_statementlarga_bolinadi():
    from analytics import consumer as analytics_consumer

    client = mock.Mock()
    with mock.patch.object(analytics_consumer, "_clickhouse_client", return_value=client):
        analytics_consumer.ensure_schema()

    statements = [c.args[0] for c in client.command.call_args_list]
    # Har bir statement ALOHIDA yuboriladi: ClickHouse bitta so'rovda
    # bir nechtasini qabul qilmaydi
    assert all(s.startswith(("CREATE TABLE IF NOT EXISTS", "ALTER TABLE")) for s in statements)
    tables = {s.split("CREATE TABLE IF NOT EXISTS ")[1].split()[0]
              for s in statements if s.startswith("CREATE TABLE")}
    assert tables == {"fact_booking", "dim_doctor", "daily_booking_agg"}
    # A10: eski jadvalga ustun — idempotent
    assert "ALTER TABLE fact_booking ADD COLUMN IF NOT EXISTS client_hash String" in statements
    # E14: xom jadval abadiy o'smaydi
    assert any("MODIFY TTL created_date" in s and "DELETE" in s for s in statements)
    # Izohlar va `;` tozalangan, `{}` o'rniga haqiqiy qiymat qo'yilgan
    assert not any("--" in s or ";" in s or "{" in s for s in statements)
