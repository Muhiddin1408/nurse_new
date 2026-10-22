from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from medbron_common.audit import Actor, record_audit
from medbron_common.context import client_ip, request_id_var
from medbron_common.phone import InvalidPhone, mask_phone, normalize_uz_phone
from medbron_common.security import CurrentAdmin, CurrentUser
from medbron_common.sms import (
    ESKIZ_TOKEN_KEY,
    ConsoleSmsClient,
    EskizSmsClient,
    MemoryTokenStore,
    RedisTokenStore,
    SmsConfig,
    SmsSendError,
    create_sms_client,
)
from medbron_common.workers import DailyAt, Every, PeriodicJob, run_periodic

# --- phone -----------------------------------------------------------------------------------


def test_normalize_uz_phone() -> None:
    assert normalize_uz_phone("+998 99 756-71-95") == "+998997567195"
    assert normalize_uz_phone("998997567195") == "+998997567195"
    for bad in ("+79001234567", "+99899756719", "+9989975671950", ""):
        with pytest.raises(InvalidPhone):
            normalize_uz_phone(bad)


def test_mask_phone() -> None:
    assert mask_phone("+998901234567") == "+99890***4567"
    assert mask_phone("123") == "***"


# --- sms -------------------------------------------------------------------------------------


class Eskiz:
    """Soxta Eskiz: login → token, sms/send → id. `script` — navbatdagi javoblar."""

    def __init__(self, script: list[int] | None = None) -> None:
        self.script = list(script or [])
        self.logins = 0
        self.sends: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            self.logins += 1
            return httpx.Response(200, json={"data": {"token": f"tok-{self.logins}"}})
        self.sends.append(request)
        status = self.script.pop(0) if self.script else 200
        if status == 200:
            return httpx.Response(200, json={"id": "msg-1", "status": "waiting"})
        return httpx.Response(status, text="error")


def eskiz(fake: Eskiz, store: MemoryTokenStore | None = None) -> EskizSmsClient:
    return EskizSmsClient(
        base_url="https://notify.eskiz.uz",
        email="e@x.uz",
        password="p",
        sender="4546",
        token_store=store,
        transport=httpx.MockTransport(fake),
    )


async def test_eskiz_send_caches_token_and_strips_plus() -> None:
    fake = Eskiz()
    store = MemoryTokenStore()
    client = eskiz(fake, store)
    assert await client.send("+998901234567", "kod 1") == "msg-1"
    assert await client.send("+998901234567", "kod 2") == "msg-1"
    assert fake.logins == 1
    assert await store.get(ESKIZ_TOKEN_KEY) == "tok-1"
    body = fake.sends[0].content.decode()
    assert "mobile_phone=998901234567" in body and "from=4546" in body
    assert fake.sends[0].headers["authorization"] == "Bearer tok-1"
    await client.aclose()


async def test_eskiz_relogins_once_on_401() -> None:
    fake = Eskiz([401, 200])
    client = eskiz(fake)
    assert await client.send("+998901234567", "t") == "msg-1"
    assert fake.logins == 2
    assert fake.sends[1].headers["authorization"] == "Bearer tok-2"


async def test_eskiz_errors_and_breaker() -> None:
    fake = Eskiz([400])
    client = eskiz(fake)
    with pytest.raises(SmsSendError, match="400"):
        await client.send("+998901234567", "t")
    assert client.breaker.state == "closed"  # 4xx provayder sog'ligini bildirmaydi

    fake.script = [500] * 5
    for _ in range(5):
        with pytest.raises(SmsSendError):
            await client.send("+998901234567", "t")
    assert client.breaker.state == "open"
    sends = len(fake.sends)
    with pytest.raises(SmsSendError, match="circuit"):
        await client.send("+998901234567", "t")
    assert len(fake.sends) == sends  # ochiq zanjir — so'rov ketmadi


async def test_eskiz_network_error_and_bad_payloads() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = EskizSmsClient(
        base_url="https://x", email="e", password="p", sender="s", transport=httpx.MockTransport(boom)
    )
    with pytest.raises(SmsSendError, match="login"):
        await client.send("+998901234567", "t")

    def no_id(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"data": {}})
        return httpx.Response(200, json={})

    client = EskizSmsClient(
        base_url="https://x", email="e", password="p", sender="s", transport=httpx.MockTransport(no_id)
    )
    with pytest.raises(SmsSendError, match="token"):
        await client.send("+998901234567", "t")


def test_eskiz_requires_credentials() -> None:
    with pytest.raises(ValueError, match="majburiy"):
        EskizSmsClient(base_url="https://x", email="", password="", sender="s")


async def test_create_sms_client() -> None:
    console = create_sms_client(SmsConfig(provider="console"), environment="local")
    assert isinstance(console, ConsoleSmsClient)
    assert await console.send("+998901234567", "salom") == "console-1"
    assert console.sent == [("+998901234567", "salom")]
    with pytest.raises(ValueError, match="prod"):
        create_sms_client(SmsConfig(provider="console"), environment="prod")
    real = create_sms_client(
        SmsConfig(provider="eskiz", eskiz_email="e", eskiz_password=SecretStr("p")), environment="prod"
    )
    assert isinstance(real, EskizSmsClient)
    await real.aclose()
    with pytest.raises(ValueError, match="Noma'lum"):
        create_sms_client(SmsConfig(provider="x"), environment="local")  # type: ignore[arg-type]


async def test_redis_token_store() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.data: dict[str, tuple[bytes, int]] = {}

        async def get(self, key: str) -> bytes | None:
            item = self.data.get(key)
            return item[0] if item else None

        async def set(self, key: str, value: str, ex: int) -> None:
            self.data[key] = (value.encode(), ex)

    redis = FakeRedis()
    store = RedisTokenStore(redis)
    assert await store.get("k") is None
    await store.set("k", "v", 10)
    assert await store.get("k") == "v" and redis.data["k"][1] == 10


# --- audit -----------------------------------------------------------------------------------


def test_record_audit_builds_valid_event() -> None:
    from medbron_common.eventbus import Outbox

    added: list[object] = []

    class Model:
        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    session = SimpleNamespace(add=added.append)
    outbox = Outbox(Model, producer="auth-service")  # type: ignore[arg-type]
    admin = CurrentAdmin(admin_id=uuid.uuid4(), permissions=frozenset())
    token = request_id_var.set("req-9")
    try:
        env = record_audit(
            outbox,
            session,
            Actor.admin(admin),  # type: ignore[arg-type]
            action="user.block",
            object_type="user",
            object_id=uuid.uuid4(),
            before={"a": 1},
            after={"a": 2},
            ip="10.0.0.1",
        )
    finally:
        request_id_var.reset(token)
    assert env["data"]["actor_type"] == "admin" and env["data"]["request_id"] == "req-9"
    assert len(added) == 1
    user = CurrentUser(user_id=uuid.uuid4(), role="doctor", roles=frozenset({"doctor"}))
    assert Actor.user(user).actor_role == "doctor"
    assert Actor.system().actor_id is None


def test_client_ip_prefers_kong_real_ip() -> None:
    req = SimpleNamespace(headers={"x-real-ip": "1.2.3.4"}, client=SimpleNamespace(host="172.18.0.5"))
    assert client_ip(req) == "1.2.3.4"
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="172.18.0.5"))
    assert client_ip(req) == "172.18.0.5"
    assert client_ip(SimpleNamespace(headers={}, client=None)) is None
    req = SimpleNamespace(headers={"x-real-ip": "not-an-ip"}, client=SimpleNamespace(host="testclient"))
    assert client_ip(req) is None
    req = SimpleNamespace(headers={"x-real-ip": "bad"}, client=SimpleNamespace(host="::1"))
    assert client_ip(req) == "::1"


# --- periodic --------------------------------------------------------------------------------


def test_schedules() -> None:
    now = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)  # 06:00 Toshkent
    assert DailyAt(7, 0).next_run(now) == datetime(2026, 10, 21, 2, 0, tzinfo=UTC)
    later = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)  # 08:00 — ertangi kun
    assert DailyAt(7, 0).next_run(later) == datetime(2026, 10, 22, 2, 0, tzinfo=UTC)
    assert Every(60).next_run(now) == datetime(2026, 10, 21, 1, 1, tzinfo=UTC)


async def test_run_periodic_survives_errors_and_stops() -> None:
    calls: list[str] = []

    async def ok() -> int:
        calls.append("ok")
        return 1

    async def bad() -> None:
        calls.append("bad")
        raise RuntimeError("x")

    stop = asyncio.Event()

    async def stopper() -> None:
        await asyncio.sleep(0.35)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_periodic(
                stop,
                [PeriodicJob("ok", Every(0.1), ok, run_on_start=True), PeriodicJob("bad", Every(0.1), bad)],
            ),
            stopper(),
        ),
        timeout=5,
    )
    assert calls.count("ok") >= 3 and calls.count("bad") >= 2
