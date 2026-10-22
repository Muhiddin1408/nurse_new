from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from medbron_common.eventbus import EventConsumer, Outbox, build_envelope
from medbron_common.eventbus.consumer import ConsumerConfigError
from medbron_common.eventbus.envelope import new_event_id
from medbron_common.eventbus.outbox import NotAPublisher, UnknownEvent
from medbron_common.events import CATALOG, Events, EventSchemaError, validate_event


class FakeSession:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)


class FakeRow:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def registered_data() -> dict:
    return {"user_id": uuid.uuid4(), "phone": "+998901234567", "registered_at": datetime.now(UTC)}


def test_catalog_constants_match() -> None:
    assert Events.BOOKING_CONFIRMED == "booking.confirmed"
    assert set(CATALOG) == {v for k, v in vars(Events).items() if k.isupper()}


def test_uuid7_is_time_ordered_and_versioned() -> None:
    ids = [new_event_id() for _ in range(50)]
    assert all(i.version == 7 for i in ids)
    assert [i.int >> 80 for i in ids] == sorted(i.int >> 80 for i in ids)


def test_envelope_valid_against_schema() -> None:
    env = build_envelope(
        event_type=Events.USER_REGISTERED,
        producer="auth-service",
        aggregate_type="user",
        aggregate_id=uuid.uuid4(),
        data=registered_data(),
    )
    assert env["occurred_at"].endswith("Z")
    validate_event(env)


def test_schema_mismatch_detected() -> None:
    data = registered_data() | {"unexpected": 1}
    env = build_envelope(
        event_type=Events.USER_REGISTERED, producer="auth-service", aggregate_type="user", aggregate_id="x", data=data
    )
    with pytest.raises(EventSchemaError) as exc:
        validate_event(env)
    assert "unexpected" in str(exc.value)


def test_decimal_and_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="tiyin"):
        build_envelope(
            event_type="a.b", producer="p", aggregate_type="x", aggregate_id="1", data={"amount": Decimal("1.00")}
        )
    with pytest.raises(ValueError, match="naive"):
        build_envelope(
            event_type="a.b", producer="p", aggregate_type="x", aggregate_id="1", data={"at": datetime(2026, 1, 1)}
        )


def test_outbox_add_event_validates_producer_and_schema() -> None:
    outbox = Outbox(FakeRow, producer="auth-service")  # type: ignore[arg-type]
    session = FakeSession()
    env = outbox.add_event(
        session,
        Events.USER_REGISTERED,
        aggregate_id=uuid.uuid4(),  # type: ignore[arg-type]
        data=registered_data(),
    )
    row = session.added[0]
    assert row.status == "pending" and row.payload == env and row.aggregate_type == "user"

    with pytest.raises(NotAPublisher):
        Outbox(FakeRow, producer="booking-service").add_event(  # type: ignore[arg-type]
            session, Events.USER_REGISTERED, aggregate_id="1", data=registered_data()
        )  # type: ignore[arg-type]
    with pytest.raises(UnknownEvent):
        outbox.add_event(session, "nope.happened", aggregate_id="1", data={})  # type: ignore[arg-type]
    with pytest.raises(EventSchemaError):
        outbox.add_event(session, Events.USER_REGISTERED, aggregate_id="1", data={"user_id": "x"})  # type: ignore[arg-type]


def test_consumer_registration_enforces_catalog() -> None:
    consumer = EventConsumer(
        service_name="booking-service",
        amqp_url="amqp://x",
        session_factory=None,  # type: ignore[arg-type]
        processed_model=None,
    )  # type: ignore[arg-type]

    @consumer.on(Events.PAYMENT_CAPTURED)
    async def handler(session, event) -> None: ...

    assert consumer.routing_keys == ["payment.captured"]
    with pytest.raises(ConsumerConfigError, match="consumer'i emas"):
        consumer.on(Events.USER_REGISTERED)
    with pytest.raises(ConsumerConfigError, match="katalogda yo'q"):
        consumer.on("rating.new")  # Trasso #4: publisher'i yo'q kalit
    with pytest.raises(ConsumerConfigError, match="allaqachon"):
        consumer.on(Events.PAYMENT_CAPTURED)


def test_sample_data_is_valid_for_every_event() -> None:
    from medbron_common.events import CATALOG, EventSchemaError
    from medbron_common.testing import sample_data

    for event_type in CATALOG:
        data = sample_data(event_type)
        assert isinstance(data, dict)
    assert sample_data("booking.created", status="confirmed")["status"] == "confirmed"
    import pytest as _pytest

    with _pytest.raises(EventSchemaError):
        sample_data("booking.created", status="bogus")
