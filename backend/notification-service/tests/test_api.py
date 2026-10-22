"""HTTP API: sozlamalar, qurilmalar, Telegram bog'lash va webhook, admin shablonlar/jurnal."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token, sample_data

from app.channels.telegram import TelegramClient
from app.services.contacts import make_link_token, read_link_token

WEBHOOK = {"X-Telegram-Bot-Api-Secret-Token": "tg-secret"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_auth(client: TestClient, admin_keys) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert client.get("/v1/notifications/preferences").status_code == 401
    r = client.get("/v1/notifications/preferences", headers=bearer(mint_admin_token(admin_keys, permissions=["*"])))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get(
        "/internal/v1/notification/ping", headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"}
    )
    assert r.status_code == 403


def test_link_token_roundtrip() -> None:
    uid = uuid.uuid4()
    token = make_link_token(uid)
    assert len(token) <= 64 and read_link_token(token) == uid
    assert read_link_token(token[:-2] + "AA") is None
    assert read_link_token(make_link_token(uid, now=0)) is None  # muddati o'tgan
    assert read_link_token("!!!") is None


@pytest.mark.integration
def test_preferences_and_devices(api: TestClient, db, user_keys) -> None:
    uid = uuid.uuid4()
    h = bearer(mint_user_token(user_keys, user_id=uid))
    assert api.get("/v1/notifications/preferences", headers=h).json() == {
        "push_enabled": True, "telegram_enabled": True, "sms_enabled": True, "marketing_opt_in": False,
        "telegram_linked": False,
    }  # fmt: skip
    r = api.patch("/v1/notifications/preferences", json={"marketing_opt_in": True, "sms_enabled": False}, headers=h)
    assert r.json()["marketing_opt_in"] is True and r.json()["sms_enabled"] is False
    assert db.scalar("SELECT marketing_changed_at IS NOT NULL FROM contacts WHERE user_id = :u", u=str(uid))
    assert api.patch("/v1/notifications/preferences", json={"phone": "x"}, headers=h).status_code == 422

    device = {"token": "fcm-token-1234567890", "platform": "android"}
    assert api.post("/v1/notifications/devices", json=device, headers=h).status_code == 204
    # Bitta telefonda akkaunt almashdi — token yangi egaga o'tadi
    other = uuid.uuid4()
    assert api.post("/v1/notifications/devices", json=device, headers=bearer(mint_user_token(user_keys, user_id=other)))
    assert db.scalar("SELECT user_id::text FROM push_devices WHERE token = :t", t=device["token"]) == str(other)
    api.request("DELETE", "/v1/notifications/devices", json={"token": device["token"]},
                headers=bearer(mint_user_token(user_keys, user_id=other)))  # fmt: skip
    assert db.scalar("SELECT count(*) FROM push_devices") == 0


@pytest.mark.integration
def test_telegram_link_and_bot_commands(api: TestClient, db, user_keys, run_handler) -> None:
    sent: list[dict] = []

    def bot(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    api.app.state.telegram = TelegramClient("123:x", transport=httpx.MockTransport(bot))
    doctor_user, doctor = uuid.uuid4(), str(uuid.uuid4())
    run_handler("doctor.approved", sample_data("doctor.approved", doctor_id=doctor, user_id=str(doctor_user)))
    start = datetime.now(UTC).replace(hour=6, minute=0, second=0, microsecond=0)  # 11:00 Toshkent — bugun
    run_handler("booking.created", sample_data("booking.created", doctor_id=doctor, status="confirmed", number="MB-42",
                                               start_at=start.isoformat()))  # fmt: skip

    link = api.post("/v1/telegram/link-token", headers=bearer(mint_user_token(user_keys, user_id=doctor_user))).json()
    assert link["url"].startswith("https://t.me/") and link["linked"] is False
    token = link["url"].split("start=")[1]

    def update(text: str, chat: int = 5001) -> int:
        return api.post("/v1/telegram/webhook", json={"message": {"chat": {"id": chat}, "text": text}},
                        headers=WEBHOOK).status_code  # fmt: skip

    assert (
        api.post("/v1/telegram/webhook", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "bad"}).status_code == 404
    )
    assert update(f"/start {token}") == 200
    assert db.scalar("SELECT telegram_chat_id FROM contacts WHERE user_id = :u", u=str(doctor_user)) == 5001
    assert "ulandi" in sent[-1]["text"]
    assert update("/today") == 200
    assert "#MB-42" in sent[-1]["text"] and "Bugun: 1 ta qabul" in sent[-1]["text"]
    assert update("/tomorrow") == 200 and "qabullar yo'q" in sent[-1]["text"]
    update("/help")
    assert "/today" in sent[-1]["text"]
    update("salom", chat=6000)
    assert "havolasi orqali" in sent[-1]["text"]
    update("/start bad-token", chat=6000)
    assert "yaroqsiz" in sent[-1]["text"]
    # Shu chat boshqa akkauntga bog'lansa — eski bog'lanish uziladi
    client = uuid.uuid4()
    update(f"/start {make_link_token(client)}")
    assert db.scalar("SELECT telegram_chat_id FROM contacts WHERE user_id = :u", u=str(doctor_user)) is None
    update("/today")
    assert "faqat tasdiqlangan shifokorlar" in sent[-1]["text"]
    update("/stop")
    assert db.scalar("SELECT telegram_enabled FROM contacts WHERE user_id = :u", u=str(client)) is False


@pytest.mark.integration
def test_admin_templates_and_logs(api: TestClient, db, admin_keys) -> None:
    h = bearer(mint_admin_token(admin_keys, permissions=["notifications.manage"]))
    items = api.get("/v1/admin/notifications/templates", headers=h).json()
    item = next(i for i in items if i["key"] == "booking_cancelled" and i["language"] == "uz")
    assert item["placeholders"] == ["number"] and item["override_text"] is None
    bad = api.put("/v1/admin/notifications/templates/booking_cancelled/uz", json={"text": "Matn {when}"}, headers=h)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "INVALID_TEMPLATE"
    assert api.put("/v1/admin/notifications/templates/nope/uz", json={"text": "x"}, headers=h).status_code == 422
    assert (
        api.put("/v1/admin/notifications/templates/booking_cancelled/en", json={"text": "x"}, headers=h).status_code
        == 422
    )
    ok = api.put("/v1/admin/notifications/templates/booking_cancelled/uz", json={"text": "Bekor: #{number}"}, headers=h)
    assert ok.status_code == 204
    item = next(i for i in api.get("/v1/admin/notifications/templates", headers=h).json()
                if i["key"] == "booking_cancelled" and i["language"] == "uz")  # fmt: skip
    assert item["override_text"] == "Bekor: #{number}"
    assert api.delete("/v1/admin/notifications/templates/booking_cancelled/uz", headers=h).status_code == 204
    assert db.scalar("SELECT count(*) FROM templates") == 0

    uid = str(uuid.uuid4())
    db.scalar("INSERT INTO notifications (id, audience, user_id, template, params, created_at) VALUES "
              "(:i, 'user', :u, 'booking_cancelled', '{}', :c)", i=str(uuid.uuid4()), u=uid,
              c=datetime.now(UTC) - timedelta(minutes=1))  # fmt: skip
    logs = api.get("/v1/admin/notifications/logs", params={"user_id": uid}, headers=h).json()
    assert len(logs) == 1 and logs[0]["status"] == "pending"
    assert api.get("/v1/admin/notifications/logs", params={"status": "sent"}, headers=h).json() == []
    no = bearer(mint_admin_token(admin_keys, permissions=["users.manage"]))
    assert api.get("/v1/admin/notifications/logs", headers=no).status_code == 403


@pytest.mark.integration
def test_daily_digest(db, run_handler) -> None:
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.services import digest

    doctor_user, doctor = str(uuid.uuid4()), str(uuid.uuid4())
    run_handler("doctor.approved", sample_data("doctor.approved", doctor_id=doctor, user_id=doctor_user))
    today = datetime.now(UTC).replace(hour=4, minute=30, second=0, microsecond=0)  # 09:30 Toshkent
    for minutes in (0, 60):
        start = (today + timedelta(minutes=minutes)).isoformat()
        run_handler(
            "booking.created", sample_data("booking.created", doctor_id=doctor, status="confirmed", start_at=start)
        )

    async def go() -> int:
        engine = create_async_engine(db.url, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                first = await digest.enqueue_daily(session, now=today)
            async with factory() as session:
                second = await digest.enqueue_daily(session, now=today)
            return first + second * 10
        finally:
            await engine.dispose()

    assert asyncio.run(go()) == 1  # ikkinchi ishga tushish — takror yo'q
    params = db.scalar("SELECT params FROM notifications WHERE template = 'doctor_daily_summary'")
    assert params["count"] == 2
