from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import aio_pika
import pytest
from sqlalchemy import func, select, update

from medbron_common.eventbus import (
    EventConsumer,
    EventPublisher,
    Outbox,
    OutboxRelay,
    PermanentError,
    build_envelope,
)
from medbron_common.eventbus.publisher import build_message
from medbron_common.events import Events

from .conftest import Note, OutboxEvent, ProcessedEvent, drain_dlq

pytestmark = pytest.mark.integration


def registered(user_id: uuid.UUID | None = None) -> dict:
    return {"user_id": user_id or uuid.uuid4(), "phone": "+998901234567", "registered_at": datetime.now(UTC)}


def make_consumer(service: str, amqp_url, session_factory, topology, **kw) -> EventConsumer:
    return EventConsumer(
        service_name=service,
        queue_name=f"{topology.exchange}.{service}",
        amqp_url=amqp_url,
        session_factory=session_factory,
        processed_model=ProcessedEvent,
        topology=topology,
        **kw,
    )


async def wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("shart bajarilmadi")
        await asyncio.sleep(0.05)


async def test_outbox_relay_consumer_end_to_end(amqp_url, session_factory, topology) -> None:
    received: list = []
    consumer = make_consumer("user-service", amqp_url, session_factory, topology)

    @consumer.on(Events.USER_REGISTERED)
    async def handle(session, event) -> None:
        session.add(Note(id=f"profile:{event.data['user_id']}"))
        received.append(event)

    await consumer.start()
    publisher = EventPublisher(amqp_url, topology=topology)
    outbox = Outbox(OutboxEvent, producer="auth-service")
    user_id = uuid.uuid4()
    try:
        async with session_factory() as s, s.begin():
            s.add(Note(id=f"user:{user_id}"))
            outbox.add_event(s, Events.USER_REGISTERED, aggregate_id=user_id, data=registered(user_id))

        relay = OutboxRelay(session_factory, OutboxEvent, publisher)
        assert await relay.run_once() == 1
        await wait_until(lambda: len(received) == 1)
    finally:
        await consumer.stop()
        await publisher.close()

    assert received[0].data["user_id"] == str(user_id)
    assert received[0].producer == "auth-service"
    async with session_factory() as s:
        row = await s.scalar(select(OutboxEvent))
        assert row.status == "published" and row.published_at is not None
        assert await s.scalar(select(func.count()).select_from(ProcessedEvent)) == 1
        assert await s.get(Note, f"profile:{user_id}") is not None


async def test_duplicate_delivery_handled_once(amqp_url, session_factory, topology) -> None:
    calls: list = []
    consumer = make_consumer("user-service", amqp_url, session_factory, topology)

    @consumer.on(Events.USER_REGISTERED)
    async def handle(session, event) -> None:
        calls.append(event.event_id)

    await consumer.start()
    publisher = EventPublisher(amqp_url, topology=topology)
    env = build_envelope(
        event_type=Events.USER_REGISTERED,
        producer="auth-service",
        aggregate_type="user",
        aggregate_id="u1",
        data=registered(),
    )
    try:
        for _ in range(3):  # at-least-once: broker bir xabarni bir necha marta yetkazishi normal
            await publisher.publish(env)
        await asyncio.sleep(1.0)
    finally:
        await consumer.stop()
        await publisher.close()
    assert len(calls) == 1


async def test_transient_error_retried_only_for_failing_consumer(amqp_url, session_factory, topology) -> None:
    """Retry xabari faqat yiqilgan consumer navbatiga qaytadi — boshqalar uni qayta olmaydi."""
    flaky_calls: list = []
    stable_calls: list = []
    flaky = make_consumer("user-service", amqp_url, session_factory, topology)
    stable = make_consumer("notification-service", amqp_url, session_factory, topology)

    @flaky.on(Events.USER_REGISTERED)
    async def flaky_handler(session, event) -> None:
        flaky_calls.append(event.event_id)
        session.add(Note(id=f"flaky:{len(flaky_calls)}"))
        if len(flaky_calls) < 3:
            raise ConnectionError("vaqtinchalik")

    @stable.on(Events.USER_REGISTERED)
    async def stable_handler(session, event) -> None:
        stable_calls.append(event.event_id)

    # Ikkala consumer bitta processed_events jadvalidan foydalanmasligi kerak (har servisda o'z bazasi).
    # Testda bitta baza bo'lgani uchun stable consumer'ga alohida "baza" sifatida schema'siz modelni
    # ishlata olmaymiz — shuning uchun stable handler idempotentlik jadvaliga yozilishini tekshirmaymiz,
    # balki faqat qayta yetkazilmaganini tekshiramiz.
    stable.processed_model = _SeparateProcessed
    await _ensure_separate_table(session_factory)

    await flaky.start()
    await stable.start()
    publisher = EventPublisher(amqp_url, topology=topology)
    try:
        await publisher.publish(
            build_envelope(
                event_type=Events.USER_REGISTERED,
                producer="auth-service",
                aggregate_type="user",
                aggregate_id="u",
                data=registered(),
            )
        )
        await wait_until(lambda: len(flaky_calls) == 3)
        await asyncio.sleep(1.0)
    finally:
        await flaky.stop()
        await stable.stop()
        await publisher.close()

    assert len(stable_calls) == 1
    assert len(flaky_calls) == 3
    async with session_factory() as s:
        # Faqat muvaffaqiyatli urinishning biznes yozuvi qoldi — yiqilganlari rollback bo'ldi.
        notes = (await s.scalars(select(Note.id).where(Note.id.like("flaky:%")))).all()
        assert notes == ["flaky:3"]
    assert await drain_dlq(amqp_url, topology, expect=0, timeout=0.5) == []


async def test_retries_exhausted_go_to_dlq(amqp_url, session_factory, topology) -> None:
    calls: list = []
    consumer = make_consumer("user-service", amqp_url, session_factory, topology, max_retries=2)

    @consumer.on(Events.USER_REGISTERED)
    async def handle(session, event) -> None:
        calls.append(1)
        session.add(Note(id=f"attempt:{len(calls)}"))
        raise TimeoutError("db lock")

    await consumer.start()
    publisher = EventPublisher(amqp_url, topology=topology)
    try:
        await publisher.publish(
            build_envelope(
                event_type=Events.USER_REGISTERED,
                producer="auth-service",
                aggregate_type="user",
                aggregate_id="u",
                data=registered(),
            )
        )
        dead = await drain_dlq(amqp_url, topology, expect=1)
    finally:
        await consumer.stop()
        await publisher.close()

    assert len(calls) == 3  # 1 + 2 retry
    assert len(dead) == 1
    headers = dead[0].headers
    assert headers["x-dead-letter-reason"] == "retries_exhausted"
    assert headers["x-retry-count"] == 2
    assert "TimeoutError" in headers["x-last-error"]
    assert dead[0].type == Events.USER_REGISTERED
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Note)) == 0
        assert await s.scalar(select(func.count()).select_from(ProcessedEvent)) == 0


async def test_permanent_errors_go_straight_to_dlq(amqp_url, session_factory, topology) -> None:
    calls: list = []
    consumer = make_consumer("user-service", amqp_url, session_factory, topology)

    @consumer.on(Events.USER_REGISTERED)
    async def handle(session, event) -> None:
        calls.append(event.data.get("phone"))
        if event.data["phone"] == "+998900000000":
            raise PermanentError("ma'nosiz event")

    await consumer.start()
    conn = await aio_pika.connect(amqp_url)
    try:
        channel = await conn.channel()
        exchange = await channel.get_exchange(topology.exchange)
        good = build_envelope(
            event_type=Events.USER_REGISTERED,
            producer="auth-service",
            aggregate_type="user",
            aggregate_id="u",
            data=registered(),
        )
        # 1) yaroqsiz JSON
        await exchange.publish(
            aio_pika.Message(b"{not json", type=Events.USER_REGISTERED), routing_key=Events.USER_REGISTERED
        )
        # 2) sxema mos emas
        bad_schema = json.loads(json.dumps(good)) | {"event_id": str(uuid.uuid4())}
        bad_schema["data"]["phone"] = 12345
        await exchange.publish(build_message(bad_schema), routing_key=Events.USER_REGISTERED)
        # 3) noma'lum versiya
        v2 = json.loads(json.dumps(good)) | {"event_id": str(uuid.uuid4()), "event_version": 2}
        await exchange.publish(build_message(v2), routing_key=Events.USER_REGISTERED)
        # 4) handler PermanentError
        perm = json.loads(json.dumps(good)) | {"event_id": str(uuid.uuid4())}
        perm["data"]["phone"] = "+998900000000"
        await exchange.publish(build_message(perm), routing_key=Events.USER_REGISTERED)
        dead = await drain_dlq(amqp_url, topology, expect=4)
    finally:
        await conn.close()
        await consumer.stop()

    reasons = Counter(m.headers["x-dead-letter-reason"] for m in dead)
    assert reasons == Counter({"invalid_envelope": 1, "schema_mismatch": 1, "unknown_version": 1, "permanent_error": 1})
    assert calls == ["+998900000000"]  # faqat 4-holat handlergacha yetdi, retry qilinmadi
    assert all(m.headers["x-source-queue"] == consumer.queue_name for m in dead)


class FailingPublisher:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.published: list = []

    async def publish(self, envelope, *, traceparent=None) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("broker yo'q")
        self.published.append(envelope["event_id"])


async def _add(session_factory, n: int = 1) -> None:
    outbox = Outbox(OutboxEvent, producer="auth-service")
    async with session_factory() as s, s.begin():
        for _ in range(n):
            outbox.add_event(s, Events.USER_REGISTERED, aggregate_id=uuid.uuid4(), data=registered())


async def _make_due(session_factory) -> None:
    async with session_factory() as s, s.begin():
        await s.execute(update(OutboxEvent).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))


async def test_relay_backoff_and_failed(session_factory) -> None:
    await _add(session_factory)
    pub = FailingPublisher(fail_times=10)
    relay = OutboxRelay(session_factory, OutboxEvent, pub, max_attempts=2, backoff_base=60)

    assert await relay.run_once() == 1
    async with session_factory() as s:
        row = await s.scalar(select(OutboxEvent))
        assert row.status == "pending" and row.attempts == 1
        assert "ConnectionError" in row.last_error
        assert row.next_attempt_at > datetime.now(UTC) + timedelta(seconds=50)
    # Muddat kelmaguncha qayta urinilmaydi
    assert await relay.run_once() == 0

    await _make_due(session_factory)
    await relay.run_once()
    async with session_factory() as s:
        row = await s.scalar(select(OutboxEvent))
        assert row.status == "failed" and row.attempts == 2


async def test_relay_recovers_after_transient_failure(session_factory) -> None:
    await _add(session_factory)
    pub = FailingPublisher(fail_times=1)
    relay = OutboxRelay(session_factory, OutboxEvent, pub, backoff_base=0)
    await relay.run_once()
    await relay.run_once()
    async with session_factory() as s:
        row = await s.scalar(select(OutboxEvent))
    assert row.status == "published" and row.attempts == 1 and row.last_error is None
    assert len(pub.published) == 1


async def test_concurrent_relays_publish_each_event_once(session_factory) -> None:
    await _add(session_factory, n=40)

    class SlowPublisher:
        def __init__(self) -> None:
            self.published: list = []

        async def publish(self, envelope, *, traceparent=None) -> None:
            await asyncio.sleep(0.005)
            self.published.append(envelope["event_id"])

    pub = SlowPublisher()
    relays = [OutboxRelay(session_factory, OutboxEvent, pub, batch_size=7) for _ in range(4)]
    for _ in range(10):
        await asyncio.gather(*(r.run_once() for r in relays))
    assert len(pub.published) == 40
    assert len(set(pub.published)) == 40  # SKIP LOCKED: hech bir event ikki marta chiqmadi


async def test_purge_removes_only_old_published(session_factory) -> None:
    await _add(session_factory, n=3)
    relay = OutboxRelay(session_factory, OutboxEvent, FailingPublisher(0), retention=timedelta(days=7))
    await relay.run_once()
    async with session_factory() as s, s.begin():
        ids = (await s.scalars(select(OutboxEvent.id).order_by(OutboxEvent.id))).all()
        await s.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == ids[0])
            .values(published_at=datetime.now(UTC) - timedelta(days=8))
        )
        await s.execute(update(OutboxEvent).where(OutboxEvent.id == ids[1]).values(status="failed"))
    assert await relay.purge() == 1
    assert await relay.pending_count() == 0
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(OutboxEvent)) == 2


async def test_relay_run_stops_on_signal(session_factory) -> None:
    relay = OutboxRelay(session_factory, OutboxEvent, FailingPublisher(0), poll_interval=0.05)
    stop = asyncio.Event()
    task = asyncio.create_task(relay.run(stop))
    await _add(session_factory)
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert await relay.pending_count() == 0


# --- yordamchi: ikkinchi servis uchun alohida processed_events jadvali -----------------------------
from sqlalchemy.orm import Mapped, mapped_column

from medbron_common.eventbus import ProcessedEventMixin

from .conftest import Base


class _SeparateProcessed(ProcessedEventMixin, Base):
    __tablename__ = "processed_events_notification"
    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)


async def _ensure_separate_table(session_factory) -> None:
    async with session_factory() as s:
        conn = await s.connection()
        await conn.run_sync(lambda c: _SeparateProcessed.__table__.create(c, checkfirst=True))
        await s.commit()


async def test_processed_events_purge(amqp_url, session_factory, topology) -> None:
    consumer = make_consumer(
        "user-service", amqp_url, session_factory, topology, processed_retention=timedelta(days=30)
    )
    async with session_factory() as s, s.begin():
        s.add(
            ProcessedEvent(
                event_id=uuid.uuid4(), event_type="user.registered", processed_at=datetime.now(UTC) - timedelta(days=31)
            )
        )
        s.add(ProcessedEvent(event_id=uuid.uuid4(), event_type="user.registered"))
    assert await consumer.purge_processed() == 1
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(ProcessedEvent)) == 1


async def test_dlq_list_and_replay_to_source_queue(amqp_url, session_factory, topology) -> None:
    from medbron_common.eventbus.dlq import list_dead_letters, replay

    broken = {"on": True}
    handled: list = []
    consumer = make_consumer("user-service", amqp_url, session_factory, topology)
    other = make_consumer("notification-service", amqp_url, session_factory, topology)
    other.processed_model = _SeparateProcessed
    await _ensure_separate_table(session_factory)
    other_calls: list = []

    @consumer.on(Events.USER_REGISTERED)
    async def handle(session, event) -> None:
        if broken["on"]:
            raise PermanentError("bug")
        handled.append(event.event_id)

    @other.on(Events.USER_REGISTERED)
    async def other_handle(session, event) -> None:
        other_calls.append(event.event_id)

    await consumer.start()
    await other.start()
    publisher = EventPublisher(amqp_url, topology=topology)
    try:
        await publisher.publish(
            build_envelope(
                event_type=Events.USER_REGISTERED,
                producer="auth-service",
                aggregate_type="user",
                aggregate_id="u",
                data=registered(),
            )
        )
        await wait_until(lambda: len(other_calls) == 1)
        await asyncio.sleep(0.5)
        letters = await list_dead_letters(amqp_url, topology)
        assert [(d.source_queue, d.reason) for d in letters] == [(consumer.queue_name, "permanent_error")]
        assert await replay(amqp_url, queue=consumer.queue_name, dry_run=True, topology=topology) == 1
        assert len(await list_dead_letters(amqp_url, topology)) == 1  # dry-run hech narsani o'zgartirmaydi

        broken["on"] = False  # "bug tuzatildi"
        assert await replay(amqp_url, queue="other.queue", topology=topology) == 0
        assert await replay(amqp_url, queue=consumer.queue_name, topology=topology) == 1
        await wait_until(lambda: len(handled) == 1)
        await asyncio.sleep(0.5)
    finally:
        await consumer.stop()
        await other.stop()
        await publisher.close()
    assert await list_dead_letters(amqp_url, topology) == []
    assert len(other_calls) == 1  # replay boshqa consumerga qayta tushmadi
