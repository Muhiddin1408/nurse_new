"""Dispatcher: kanal ustuvorligi, qayta urinish, eslatma himoyasi, ops, FCM va Telegram kontrakti."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from medbron_common.sms import ConsoleSmsClient, SmsSendError
from medbron_common.testing import generate_keypair, sample_data

from app.channels.push import ConsolePush, FcmPush, InvalidToken, PushError, ServiceAccount
from app.channels.telegram import TelegramClient
from app.services.dispatcher import Channels, dispatch_once, prepare_params

pytestmark = pytest.mark.integration


class FailingSms(ConsoleSmsClient):
    async def send(self, phone: str, text: str) -> str:
        raise SmsSendError("down")


class Telegram:
    """Soxta Bot API (MockTransport): `ok` boshqariladi, yuborilganlar yoziladi."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if not self.ok:
            return httpx.Response(403, json={"ok": False, "description": "bot was blocked"})
        self.sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {}})

    def client(self) -> TelegramClient:
        return TelegramClient("123:x", transport=httpx.MockTransport(self))


def run_dispatch(db, channels: Channels) -> int:
    async def go() -> int:
        engine = create_async_engine(db.url, poolclass=NullPool)
        try:
            return await dispatch_once(async_sessionmaker(engine, expire_on_commit=False), channels)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def contact(db, *, phone: str | None = "+998901112233", language: str = "uz", chat: int | None = None) -> str:
    uid = str(uuid.uuid4())
    db.scalar(
        "INSERT INTO contacts (user_id, phone, language, telegram_chat_id) VALUES (:u, :p, :l, :c)",
        u=uid,
        p=phone,
        l=language,
        c=chat,
    )
    return uid


def enqueue(db, uid: str | None, template: str, params: dict, **extra) -> str:
    nid = str(uuid.uuid4())
    cols = {"id": nid, "audience": "ops" if uid is None else "user", "user_id": uid, "template": template,
            "params": json.dumps(params), **extra}  # fmt: skip
    names = ", ".join(cols)
    values = ", ".join(f"CAST(:{k} AS jsonb)" if k == "params" else f":{k}" for k in cols)
    db.scalar(f"INSERT INTO notifications ({names}) VALUES ({values})", **cols)  # noqa: S608 — test
    return nid


def state(db, nid: str) -> tuple:
    return tuple(db.all("SELECT status, channel, attempts, last_error FROM notifications WHERE id = :i", i=nid)[0])


def test_channel_priority_push_then_telegram_then_sms(db) -> None:
    tg = Telegram()
    push, sms = ConsolePush(), ConsoleSmsClient()
    channels = Channels(sms=sms, push=push, telegram=tg.client())

    with_device = contact(db, chat=777)
    db.scalar("INSERT INTO push_devices (id, user_id, token, platform) VALUES (:i, :u, 'tok-abcdefghij', 'ios')",
              i=str(uuid.uuid4()), u=with_device)  # fmt: skip
    a = enqueue(db, with_device, "booking_cancelled", {"number": "MB-1"})
    with_chat = contact(db, chat=888, language="ru")
    b = enqueue(db, with_chat, "refund_succeeded", {"number": "MB-2", "amount_tiyin": 12_500_000})
    phone_only = contact(db)
    c = enqueue(
        db, phone_only, "booking_confirmed", {"number": "MB-3", "when": "2026-10-25T05:00:00Z", "place": "home"}
    )
    assert run_dispatch(db, channels) == 3

    assert state(db, a)[:2] == ("sent", "push") and push.sent[0][2] == "MedBron: buyurtma #MB-1 bekor qilindi."
    assert state(db, b)[:2] == ("sent", "telegram")
    assert tg.sent[0]["chat_id"] == 888 and "возвращено 125 000 сум" in tg.sent[0]["text"]
    assert state(db, c)[:2] == ("sent", "sms")
    assert sms.sent == [("+998901112233", "MedBron: buyurtmangiz #MB-3 tasdiqlandi — 25.10 10:00. Uy chaqiruvi.")]


def test_failures_fall_back_retry_and_fail(db) -> None:
    tg = Telegram(ok=False)
    channels = Channels(sms=FailingSms(), push=ConsolePush(), telegram=tg.client())
    uid = contact(db, chat=999)
    nid = enqueue(db, uid, "booking_cancelled", {"number": "MB-9"})
    run_dispatch(db, channels)
    status, channel, attempts, error = state(db, nid)
    assert (status, channel, attempts) == ("pending", None, 1) and "telegram" in error and "sms" in error
    send_at = db.scalar("SELECT send_at FROM notifications WHERE id = :i", i=nid)
    assert send_at > datetime.now(UTC) + timedelta(seconds=30)  # backoff

    db.scalar("UPDATE notifications SET send_at = now(), attempts = 4 WHERE id = :i", i=nid)
    run_dispatch(db, channels)
    assert state(db, nid)[0] == "failed"

    # SMS o'chirilgan bo'lsa ham tranzaksion xabar SMS bilan ketadi (boshqa kanal ishlamadi)
    uid2 = contact(db)
    db.scalar("UPDATE contacts SET sms_enabled = false WHERE user_id = :u", u=uid2)
    sms = ConsoleSmsClient()
    n2 = enqueue(db, uid2, "booking_expired", {"number": "MB-5"})
    run_dispatch(db, Channels(sms=sms, push=ConsolePush(), telegram=Telegram().client()))
    assert state(db, n2)[:2] == ("sent", "sms")


def test_marketing_and_missing_contact_are_skipped(db) -> None:
    channels = Channels(sms=ConsoleSmsClient(), push=ConsolePush(), telegram=Telegram().client())
    uid = contact(db)
    m = enqueue(db, uid, "booking_cancelled", {"number": "x"}, marketing=True)
    ghost = enqueue(db, str(uuid.uuid4()), "booking_cancelled", {"number": "x"})
    uid_opt = contact(db)
    db.scalar("UPDATE contacts SET marketing_opt_in = true WHERE user_id = :u", u=uid_opt)
    m2 = enqueue(db, uid_opt, "booking_cancelled", {"number": "x"}, marketing=True)
    run_dispatch(db, channels)
    assert state(db, m)[0] == "skipped" and "marketing" in state(db, m)[3]
    assert state(db, m2)[0] == "skipped" and "SMS" in state(db, m2)[3]  # rozilik bor, lekin bepul kanal yo'q
    assert state(db, ghost)[0] == "skipped"


def test_reminder_guard_and_review_defer(db, run_handler) -> None:
    channels = Channels(sms=ConsoleSmsClient(), push=ConsolePush(), telegram=Telegram().client())
    uid = contact(db)
    booking = str(uuid.uuid4())
    run_handler(
        "booking.created", sample_data("booking.created", booking_id=booking, client_id=uid, status="confirmed")
    )
    ok = enqueue(db, uid, "booking_reminder_2h", {"when": "2026-10-25T05:00:00Z", "place": "clinic"},
                 booking_id=booking, kind="reminder")  # fmt: skip
    review = enqueue(db, uid, "booking_review_request", {"number": "MB-1"}, booking_id=booking, kind="review_request")
    run_dispatch(db, channels)
    assert state(db, ok)[0] == "sent"
    assert state(db, review)[0] == "pending" and "kutilmoqda" in state(db, review)[3]  # hali yakunlanmagan

    run_handler("booking.cancelled", sample_data("booking.cancelled", booking_id=booking, client_id=uid))
    late = enqueue(db, uid, "booking_reminder_24h", {"when": "2026-10-25T05:00:00Z", "place": "clinic"},
                   booking_id=booking, kind="reminder")  # fmt: skip
    db.scalar("UPDATE notifications SET send_at = now() WHERE id = :i", i=review)
    run_dispatch(db, channels)
    # 1-qatlam: bekor qilish eventi kutilayotgan eslatmalarni yopdi; 2-qatlam: keyin paydo bo'lgani
    # (masalan, kechikkan rejalashtirish) yuborish paytida bron holatiga qarab o'tkazib yuboriladi
    assert state(db, review)[0] == "cancelled"
    assert state(db, late)[0] == "skipped" and "cancelled" in state(db, late)[3]


def test_ops_and_template_override(db, admin_keys) -> None:
    tg = Telegram()
    no_ops = enqueue(db, None, "ops_dispute_sla_breached", {"dispute_id": "d1", "due": "x"})
    run_dispatch(db, Channels(sms=ConsoleSmsClient(), push=ConsolePush(), telegram=tg.client()))
    assert state(db, no_ops)[0] == "skipped"
    ops = enqueue(db, None, "ops_refund_failed", {"refund_id": "r1", "amount_tiyin": 100, "attempts": 3, "error": "e"})
    run_dispatch(db, Channels(sms=ConsoleSmsClient(), push=ConsolePush(), telegram=tg.client(), ops_chat_id=-100))
    assert state(db, ops)[:2] == ("sent", "telegram") and tg.sent[-1]["chat_id"] == -100

    uid = contact(db)
    db.scalar("INSERT INTO templates (key, language, text) VALUES ('booking_cancelled', 'uz', 'Yangi matn #{number}')")
    from app.services import templates

    templates.clear_cache()  # dispatcher keshi ≤ TEMPLATE_CACHE_SECONDS (60 s) eski matnni saqlashi mumkin
    sms = ConsoleSmsClient()
    nid = enqueue(db, uid, "booking_cancelled", {"number": "MB-4"})
    run_dispatch(db, Channels(sms=sms, push=ConsolePush(), telegram=tg.client()))
    assert state(db, nid)[0] == "sent" and sms.sent[-1][1] == "Yangi matn #MB-4"

    bad = enqueue(db, uid, "booking_cancelled", {})  # parametr yetishmaydi — jim emas, skipped + log
    run_dispatch(db, Channels(sms=sms, push=ConsolePush(), telegram=tg.client()))
    assert state(db, bad)[0] == "skipped" and "shablon" in state(db, bad)[3]


def test_stale_lease_is_reclaimed(db) -> None:
    uid = contact(db)
    nid = enqueue(db, uid, "booking_cancelled", {"number": "x"}, status="sending")
    db.scalar("UPDATE notifications SET locked_until = now() - interval '1 minute' WHERE id = :i", i=nid)
    run_dispatch(db, Channels(sms=ConsoleSmsClient(), push=ConsolePush(), telegram=Telegram().client()))
    assert state(db, nid)[0] == "sent"


def test_prepare_params() -> None:
    out = prepare_params({"when": "2026-10-25T05:00:00Z", "place": "clinic", "amount_tiyin": 100050}, "ru")
    assert out == {"when": "25.10 10:00", "place": "Приём в клинике", "amount": "1 000"}


async def test_fcm_contract(tmp_path: Path) -> None:
    keys = generate_keypair()
    account = ServiceAccount(
        project_id="medbron-test", client_email="push@medbron.iam", private_key=keys.private_pem.decode(),
        token_uri="https://oauth2.googleapis.com/token",
    )  # fmt: skip
    calls: list[httpx.Request] = []
    replies = {"default": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.x", "expires_in": 3600})
        code = replies["default"]
        if code == 404:
            return httpx.Response(
                404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}
            )
        return httpx.Response(code, json={"name": "projects/x/messages/1"})

    fcm = FcmPush(account, transport=httpx.MockTransport(handler))
    await fcm.send("device-token", "MedBron", "salom", {"k": "v"})
    await fcm.send("device-token", "MedBron", "salom2")
    oauth = [c for c in calls if c.url.host == "oauth2.googleapis.com"]
    assert len(oauth) == 1  # token keshlanadi
    send = calls[-1]
    assert (
        send.url.path == "/v1/projects/medbron-test/messages:send" and send.headers["authorization"] == "Bearer ya29.x"
    )
    assert json.loads(send.content)["message"]["token"] == "device-token"
    replies["default"] = 404
    with pytest.raises(InvalidToken):
        await fcm.send("dead", "t", "b")
    replies["default"] = 503
    with pytest.raises(PushError):
        await fcm.send("t", "t", "b")
    await fcm.aclose()

    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"project_id": "p1", "client_email": "e", "private_key": "k"}))
    assert ServiceAccount.from_file(sa).project_id == "p1"
    assert ServiceAccount.from_file(sa, "override").project_id == "override"
    import base64

    from app.channels.push import ConsolePush as _Console
    from app.channels.push import DisabledPush, create_push_client

    encoded = base64.b64encode(sa.read_bytes()).decode()
    assert ServiceAccount.from_json(encoded).client_email == "e"
    assert isinstance(create_push_client("fcm", None, None, encoded), FcmPush)
    assert isinstance(create_push_client("console", None, None), _Console)
    disabled = create_push_client("off", None, None)
    assert isinstance(disabled, DisabledPush) and not disabled.enabled
    with pytest.raises(PushError):
        await disabled.send("t", "t", "b")
