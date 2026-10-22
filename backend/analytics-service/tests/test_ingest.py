"""Ingest: partiyalash, ack faqat yozilgandan keyin, ClickHouse yiqilganda backoff, noto'g'ri envelope → DLX,
audit o'tkazib yuboriladi; haqiqiy RabbitMQ (`#` binding) + ClickHouse bilan uchidan-uchiga."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import aio_pika
import pytest

from medbron_common.eventbus import EventPublisher, Topology
from medbron_common.testing import sample_data

from app.clients.clickhouse import ClickHouse, ClickHouseError
from app.services.ingest import Ingestor
from tests.conftest import CH_DB, CH_PASSWORD, CH_USER, Ch, envelope


class FakeMessage:
    def __init__(self, body: bytes, event_type: str = "booking.created") -> None:
        self.body = body
        self.headers: dict[str, Any] = {}
        self.content_type = "application/json"
        self.message_id = str(uuid.uuid4())
        self.type = event_type
        self.routing_key = event_type
        self.state = "new"

    async def ack(self) -> None:
        self.state = "ack"

    async def nack(self, requeue: bool = True) -> None:
        self.state = "requeue" if requeue else "dead"


class FakeSink:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.batches: list[list[dict[str, Any]]] = []

    async def insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        if self.failures:
            self.failures -= 1
            raise ClickHouseError("clickhouse 503")
        self.batches.append(list(rows))


def _msg(event_type: str = "booking.created", **overrides: Any) -> FakeMessage:
    env = envelope(event_type, sample_data(event_type, **overrides), occurred_at=datetime.now(UTC))
    return FakeMessage(json.dumps(env).encode(), event_type)


async def test_batch_ack_after_insert_and_skips() -> None:
    sink = FakeSink()
    ing = Ingestor(sink, amqp_url="amqp://unused/", batch_size=2)
    good, audit, bad, not_obj = _msg(), _msg("audit.recorded"), FakeMessage(b"{not json"), FakeMessage(b"[1]")
    for m in (good, audit, bad, not_obj):
        await ing.accept(m)  # type: ignore[arg-type]
    assert audit.state == "ack" and bad.state == "dead" and not_obj.state == "dead"  # DLX yo'q → navbat DLX'i
    assert good.state == "new"  # partiya yozilmaguncha ack yo'q
    assert await ing.flush() is True
    assert good.state == "ack" and len(sink.batches) == 1
    row = sink.batches[0][0]
    assert row["event_type"] == "booking.created" and json.loads(row["data"])["booking_id"]
    assert await ing.flush() is True  # bo'sh partiya


async def test_clickhouse_down_backoff_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float, stop: asyncio.Event | None) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.services.ingest._sleep_or_stop", fake_sleep)
    sink = FakeSink(failures=3)
    ing = Ingestor(sink, amqp_url="amqp://unused/", max_backoff=3)
    m = _msg()
    await ing.accept(m)  # type: ignore[arg-type]
    assert await ing.flush() is True
    assert sleeps == [1.0, 2.0, 3.0] and m.state == "ack"


async def test_stop_while_clickhouse_down_requeues() -> None:
    stop = asyncio.Event()
    stop.set()
    ing = Ingestor(FakeSink(failures=1), amqp_url="amqp://unused/")
    m = _msg()
    await ing.accept(m)  # type: ignore[arg-type]
    assert await ing.flush(stop) is False
    assert m.state == "requeue"


@pytest.mark.integration
def test_end_to_end_with_rabbitmq_and_clickhouse(rabbitmq_url: str, ch: Ch) -> None:
    topology = Topology(exchange="t.events", dlx="t.dlx", dlq="t.dlq", retry_exchange="t.retry", quorum=False)
    booking_id = uuid.uuid4()
    created = envelope("booking.created", sample_data("booking.created", booking_id=str(booking_id)),
                       occurred_at=datetime.now(UTC))  # fmt: skip
    captured = envelope("payment.captured", sample_data("payment.captured", booking_id=str(booking_id)),
                        occurred_at=datetime.now(UTC))  # fmt: skip
    audit = envelope("audit.recorded", sample_data("audit.recorded"), occurred_at=datetime.now(UTC))

    async def scenario() -> int:
        sink = ClickHouse(ch.url, user=CH_USER, password=CH_PASSWORD, database=CH_DB)
        ing = Ingestor(sink, amqp_url=rabbitmq_url, batch_size=100, flush_seconds=0.3, topology=topology,
                       queue_name="analytics-test.all-events")  # fmt: skip
        stop = asyncio.Event()
        task = asyncio.create_task(ing.run(stop))
        await asyncio.sleep(1.0)  # navbat va binding e'lon qilinsin
        publisher = EventPublisher(rabbitmq_url, topology=topology)
        for env in (created, created, captured, audit):  # created ikki marta — at-least-once
            await publisher.publish(env)
        assert publisher._exchange is not None
        await publisher._exchange.publish(aio_pika.Message(b"garbage", type="x.y"), routing_key="x.y")
        await publisher.close()
        await asyncio.sleep(2.0)
        stop.set()
        await asyncio.wait_for(task, timeout=15)
        await sink.aclose()
        connection = await aio_pika.connect(rabbitmq_url)
        channel = await connection.channel()
        dlq = await channel.declare_queue("t.dlq", durable=True, passive=True)
        dead = dlq.declaration_result.message_count
        await connection.close()
        return int(dead)

    assert asyncio.run(scenario()) == 1  # faqat noto'g'ri envelope
    assert int(ch.scalar("SELECT count() FROM events_raw FINAL")) == 2  # created (dedup) + captured; audit yo'q
    types = ch.run(lambda c: c.select("SELECT event_type, booking_id FROM events_raw FINAL ORDER BY event_type"))
    assert [(t["event_type"], t["booking_id"]) for t in types] == [
        ("booking.created", str(booking_id)),
        ("payment.captured", str(booking_id)),
    ]


async def test_clickhouse_client_errors_and_params() -> None:
    import httpx

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/ping":
            return httpx.Response(500)
        if b"boom" in request.content:
            return httpx.Response(400, text="Code: 62. Syntax error")
        return httpx.Response(200, json={"data": [{"ok": 1}]})

    ch = ClickHouse("http://ch", user="u", password="p", database="db", transport=httpx.MockTransport(handler))
    assert await ch.select("SELECT {d:Date}", {"d": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).date()}) == [{"ok": 1}]
    assert seen[-1].url.params["param_d"] == "2026-01-02" and seen[-1].url.params["database"] == "db"
    assert seen[-1].headers["x-clickhouse-user"] == "u"
    await ch.select(
        "SELECT {t:DateTime64(3)}, {xs:Array(String)}", {"t": datetime(2026, 1, 2, 3, 4, 5), "xs": ["a", "b"]}
    )
    assert (
        seen[-1].url.params["param_t"] == "2026-01-02 03:04:05.000" and seen[-1].url.params["param_xs"] == "['a','b']"
    )
    await ch.insert("t", [])  # bo'sh — so'rov yo'q
    count = len(seen)
    await ch.insert("t", [{"a": 1}, {"a": 2}])
    assert len(seen) == count + 1 and seen[-1].url.params["query"] == "INSERT INTO t FORMAT JSONEachRow"
    with pytest.raises(ClickHouseError, match="Syntax"):
        await ch.command("boom")
    with pytest.raises(ClickHouseError):
        await ch.ping()
    await ch.aclose()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    broken = ClickHouse("http://ch", user="u", password="p", database="db", transport=httpx.MockTransport(down))
    with pytest.raises(ClickHouseError, match="tarmoq"):
        await broken.command("SELECT 1")
    with pytest.raises(ClickHouseError, match="tarmoq"):
        await broken.ping()
    await broken.aclose()
