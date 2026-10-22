"""Bildirishnoma sozlamalari, push qurilmalari, Telegram'ni bog'lash va bot buyruqlari."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.telegram import TelegramClient, TelegramError
from app.core.config import get_settings
from app.metrics import TELEGRAM_UPDATES
from app.models import BookingProjection, Contact
from app.repositories import notifications as repo
from app.services.templates import LOCAL_TZ

logger = logging.getLogger(__name__)

PREFERENCE_FIELDS = ("push_enabled", "telegram_enabled", "sms_enabled", "marketing_opt_in")


async def preferences(session: AsyncSession, user_id: uuid.UUID) -> Contact:
    await repo.upsert_contact(session, user_id)  # kontakt event'dan oldin ham bo'lishi mumkin
    contact = await repo.get_contact(session, user_id, for_update=True)
    assert contact is not None
    return contact


async def update_preferences(session: AsyncSession, user_id: uuid.UUID, changes: dict[str, bool]) -> Contact:
    contact = await preferences(session, user_id)
    if "marketing_opt_in" in changes and changes["marketing_opt_in"] != contact.marketing_opt_in:
        contact.marketing_changed_at = datetime.now(UTC)
    for key in PREFERENCE_FIELDS:
        if key in changes:
            setattr(contact, key, changes[key])
    return contact


async def register_device(session: AsyncSession, user_id: uuid.UUID, token: str, platform: str) -> None:
    await repo.upsert_contact(session, user_id)
    await repo.upsert_device(session, user_id, token, platform, datetime.now(UTC))


async def remove_device(session: AsyncSession, user_id: uuid.UUID, token: str) -> None:
    await repo.delete_device(session, user_id, token)


# --- Telegram bog'lash -----------------------------------------------------------------------


def _sign(payload: bytes) -> bytes:
    return hmac.new(get_settings().link_secret, payload, hashlib.sha256).digest()[:12]


def make_link_token(user_id: uuid.UUID, *, now: float | None = None) -> str:
    """`/start <token>` uchun: user_id (16) + muddat (4) + HMAC (12) → 43 belgili base64url (Telegram
    `start` parametri ≤ 64 belgi, faqat `A-Za-z0-9_-`)."""
    expires = int((time.time() if now is None else now) + get_settings().telegram_link_ttl_seconds)
    body = user_id.bytes + expires.to_bytes(4, "big")
    return base64.urlsafe_b64encode(body + _sign(body)).rstrip(b"=").decode()


def read_link_token(token: str, *, now: float | None = None) -> uuid.UUID | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if len(raw) != 32:
        return None
    body, sig = raw[:20], raw[20:]
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    if int.from_bytes(body[16:], "big") < (time.time() if now is None else now):
        return None
    return uuid.UUID(bytes=body[:16])


def link_url(user_id: uuid.UUID) -> str:
    return f"https://t.me/{get_settings().telegram_bot_username}?start={make_link_token(user_id)}"


HELP = {
    "uz": "MedBron boti\n\n/today — bugungi qabullar (shifokor)\n/tomorrow — ertangi qabullar\n"
    "/stop — xabarlarni o'chirish",
    "ru": "Бот MedBron\n\n/today — приёмы сегодня (врач)\n/tomorrow — приёмы завтра\n/stop — отключить уведомления",
}


async def _reply(telegram: TelegramClient, chat_id: int, text: str) -> None:
    try:
        await telegram.send_message(chat_id, text)
    except TelegramError as exc:
        logger.warning("telegram reply failed", extra={"chat_id": chat_id, "error": str(exc)})


async def _link(session: AsyncSession, telegram: TelegramClient, chat_id: int, token: str) -> None:
    user_id = read_link_token(token) if token else None
    if user_id is None:
        TELEGRAM_UPDATES.labels(kind="link_invalid").inc()
        await _reply(telegram, chat_id, "Havola yaroqsiz yoki muddati o'tgan. Ilovadan yangisini oling.")
        return
    await repo.upsert_contact(session, user_id)
    # Bitta chat — bitta akkaunt: boshqa akkauntga bog'langan bo'lsa — uziladi
    await repo.unlink_chat(session, chat_id, except_user=user_id)
    contact = await repo.get_contact(session, user_id, for_update=True)
    assert contact is not None
    contact.telegram_chat_id = chat_id
    contact.telegram_enabled = True
    await session.flush()
    TELEGRAM_UPDATES.labels(kind="linked").inc()
    text = (
        "✅ Telegram подключён. Уведомления о записях будут приходить сюда."
        if contact.language == "ru"
        else "✅ Telegram ulandi. Bron va eslatmalar endi shu yerga keladi (SMS o'rniga)."
    )
    await _reply(telegram, chat_id, text)


async def _day(session: AsyncSession, telegram: TelegramClient, contact: Contact, offset: int) -> None:
    """Shifokorning kunlik qabullari — booking proyeksiyasidan (sinxron chaqiruvsiz). Bemor ismi va
    manzili botda KO'RSATILMAYDI: Telegram chat tarixi qurilmadan tashqariga chiqishi mumkin."""
    doctor_ids = await repo.doctor_ids_of_user(session, contact.user_id)
    if not doctor_ids:
        await _reply(telegram, contact.telegram_chat_id or 0, "Bu buyruq faqat tasdiqlangan shifokorlar uchun.")
        return
    local_day = (datetime.now(LOCAL_TZ) + timedelta(days=offset)).date()
    start = datetime.combine(local_day, datetime.min.time(), tzinfo=LOCAL_TZ)
    rows: list[BookingProjection] = []
    for doctor_id in doctor_ids:
        rows.extend(await repo.doctor_day(session, doctor_id, start, start + timedelta(days=1)))
    title = "Bugun" if offset == 0 else "Ertaga"
    if not rows:
        await _reply(telegram, contact.telegram_chat_id or 0, f"{title} qabullar yo'q.")
        return
    lines = [f"{title}: {len(rows)} ta qabul"]
    for b in sorted(rows, key=lambda r: r.start_at):
        place = "🏠 uy chaqiruvi" if b.place == "home" else "🏥 klinika"
        lines.append(f"{b.start_at.astimezone(LOCAL_TZ):%H:%M} — #{b.number} · {place} · {b.status}")
    await _reply(telegram, contact.telegram_chat_id or 0, "\n".join(lines))


async def handle_update(session: AsyncSession, telegram: TelegramClient, update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not isinstance(chat_id, int) or not text:
        TELEGRAM_UPDATES.labels(kind="ignored").inc()
        return
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        await _link(session, telegram, chat_id, parts[1] if len(parts) > 1 else "")
        return
    contact = await repo.contact_by_chat(session, chat_id)
    if contact is None:
        TELEGRAM_UPDATES.labels(kind="unknown_chat").inc()
        await _reply(telegram, chat_id, "Botni ilovadagi «Telegram'ni ulash» havolasi orqali oching.")
        return
    TELEGRAM_UPDATES.labels(kind="command").inc()
    if text.startswith("/today"):
        await _day(session, telegram, contact, 0)
    elif text.startswith("/tomorrow"):
        await _day(session, telegram, contact, 1)
    elif text.startswith("/stop"):
        contact.telegram_enabled = False
        await _reply(telegram, chat_id, "Telegram xabarlari o'chirildi. Ilovadagi sozlamalardan qayta yoqing.")
    else:
        await _reply(telegram, chat_id, HELP.get(contact.language, HELP["uz"]))
