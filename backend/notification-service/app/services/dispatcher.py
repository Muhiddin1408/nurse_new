"""Xabarlar outbox'ini yuborish (alohida konteyner: `python -m app.workers.dispatcher`).

1. `claim_due` — vaqti kelgan qatorlar `sending` ga (lease 60 s, SKIP LOCKED); tranzaksiya darhol yopiladi,
   tarmoq chaqiruvlari DB qulfi ostida bajarilmaydi.
2. Har xabar: eslatma bo'lsa — bron holati QAYTA tekshiriladi (ikkinchi himoya qatlami: bekor qilingan bron
   uchun "ertaga qabulingiz bor" ketmaydi); kontakt va til — yuborish paytidagi.
3. Kanal ustuvorligi: push (bepul) → Telegram (bepul) → SMS (pullik). Tranzaksion xabar YETKAZILISHI shart:
   bepul kanallar ishlamasa SMS ketadi (foydalanuvchi SMS'ni o'chirgan bo'lsa ham). Marketing — rozilik
   bilan va SMS'siz.
4. Hech bir kanal ishlamasa — backoff bilan qayta (1, 5, 15, 60 daq), `DISPATCH_MAX_ATTEMPTS` dan keyin `failed`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from medbron_common.phone import mask_phone
from medbron_common.sms import SmsClient, SmsSendError

from app.channels.push import InvalidToken, PushClient, PushError
from app.channels.telegram import TelegramClient, TelegramError
from app.core.config import get_settings
from app.metrics import BACKLOG, DELIVERIES
from app.models import Notification
from app.repositories import notifications as repo
from app.services import templates
from app.services.rules import REMINDER, REVIEW_REQUEST

logger = logging.getLogger(__name__)

LEASE = timedelta(seconds=60)
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))
# Baholash so'rovi: shifokor "yakunlandi" ni kechroq bosishi mumkin — 1 soatdan keyin qayta ko'riladi, 30 marta
REVIEW_DEFER = timedelta(hours=1)
MAX_REVIEW_DEFERS = 30
PUSH_TITLE = "MedBron"


@dataclass
class Channels:
    sms: SmsClient
    push: PushClient
    telegram: TelegramClient
    ops_chat_id: int | None = None


@dataclass(frozen=True)
class Outcome:
    status: str  # sent | skipped | retry
    channel: str | None = None
    error: str | None = None
    defer_until: datetime | None = None


def prepare_params(params: dict[str, Any], language: str) -> dict[str, Any]:
    """Xom parametrlar → foydalanuvchi tilidagi matn bo'laklari."""
    out = dict(params)
    if isinstance(out.get("when"), str) and "T" in out["when"]:
        out["when"] = templates.fmt_when(out["when"])
    if isinstance(out.get("first"), str) and "T" in out["first"]:
        out["first"] = templates.fmt_when(out["first"])[-5:]
    if "place" in out:
        out["place"] = templates.place_label(str(out["place"]), language)
    if "amount_tiyin" in out:
        out["amount"] = templates.fmt_amount(int(out.pop("amount_tiyin")))
    return out


async def _booking_guard(session: AsyncSession, item: Notification) -> Outcome | None:
    """Eslatma/baholash so'rovi uchun bronning HOZIRGI holati. None — yuborish mumkin."""
    if item.kind is None or item.booking_id is None:
        return None
    booking = await repo.get_booking(session, item.booking_id)
    status = booking.status if booking else "missing"
    if item.kind == REMINDER and status != "confirmed":
        return Outcome("skipped", error=f"bron holati: {status}")
    if item.kind == REVIEW_REQUEST:
        if status in ("confirmed", "in_progress") and item.attempts < MAX_REVIEW_DEFERS:
            return Outcome("retry", error=f"kutilmoqda: {status}", defer_until=datetime.now(UTC) + REVIEW_DEFER)
        if status != "completed":
            return Outcome("skipped", error=f"bron holati: {status}")
    return None


async def _deliver_user(session: AsyncSession, channels: Channels, item: Notification) -> Outcome:
    assert item.user_id is not None
    contact = await repo.get_contact(session, item.user_id)
    if contact is None or not contact.is_active:
        return Outcome("skipped", error="kontakt yo'q yoki faol emas")
    if item.marketing and not contact.marketing_opt_in:
        return Outcome("skipped", error="marketing roziligi yo'q")
    text = await templates.render(
        session,
        item.template,
        contact.language,
        prepare_params(item.params, contact.language),
        ttl=get_settings().template_cache_seconds,
    )
    errors: list[str] = []

    if contact.push_enabled and channels.push.enabled:
        delivered = False
        for device in await repo.active_devices(session, contact.user_id):
            try:
                await channels.push.send(device.token, PUSH_TITLE, text, {"template": item.template})
                delivered = True
            except InvalidToken:
                await repo.deactivate_device(session, device.id)
            except PushError as exc:
                errors.append(f"push: {exc}")
        if delivered:
            return Outcome("sent", "push")
    if contact.telegram_enabled and contact.telegram_chat_id and channels.telegram.enabled:
        try:
            await channels.telegram.send_message(contact.telegram_chat_id, text)
            return Outcome("sent", "telegram")
        except TelegramError as exc:
            errors.append(f"telegram: {exc}")
    if item.marketing:
        return Outcome("skipped", error="; ".join(errors) or "bepul kanal yo'q (marketing SMS'siz)")
    if not contact.phone:
        return Outcome("skipped", error="; ".join([*errors, "telefon yo'q"]))
    try:
        await channels.sms.send(contact.phone, text)
        return Outcome("sent", "sms")
    except SmsSendError as exc:
        errors.append(f"sms: {exc}")
        logger.warning("sms failed", extra={"phone": mask_phone(contact.phone), "template": item.template})
    return Outcome("retry", error="; ".join(errors))


async def _deliver_ops(session: AsyncSession, channels: Channels, item: Notification) -> Outcome:
    if channels.ops_chat_id is None or not channels.telegram.enabled:
        return Outcome("skipped", error="ops kanali sozlanmagan")
    text = await templates.render(session, item.template, "uz", prepare_params(item.params, "uz"))
    try:
        await channels.telegram.send_message(channels.ops_chat_id, text)
    except TelegramError as exc:
        return Outcome("retry", error=f"telegram: {exc}")
    return Outcome("sent", "telegram")


async def deliver(session: AsyncSession, channels: Channels, item: Notification) -> Outcome:
    guard = await _booking_guard(session, item)
    if guard is not None:
        return guard
    try:
        if item.audience == "ops":
            return await _deliver_ops(session, channels, item)
        return await _deliver_user(session, channels, item)
    except templates.TemplateError as exc:
        logger.error("template error", extra={"template": item.template, "error": str(exc)})
        return Outcome("skipped", error=f"shablon: {exc}")


def _apply(item: Notification, outcome: Outcome, now: datetime, max_attempts: int) -> None:
    item.locked_until = None
    item.last_error = outcome.error
    if outcome.status == "sent":
        item.status, item.channel, item.sent_at = "sent", outcome.channel, now
        item.attempts += 1
    elif outcome.status == "skipped":
        item.status = "skipped"
    elif outcome.defer_until is not None:
        item.status, item.send_at = "pending", outcome.defer_until
        item.attempts += 1
    else:
        item.attempts += 1
        if item.attempts >= max_attempts:
            item.status = "failed"
        else:
            item.status = "pending"
            item.send_at = now + BACKOFF[min(item.attempts - 1, len(BACKOFF) - 1)]
    DELIVERIES.labels(template=item.template, result=item.status, channel=item.channel or "none").inc()


async def dispatch_once(factory: async_sessionmaker[AsyncSession], channels: Channels) -> int:
    """Bitta partiya. Qaytaradi: ishlangan xabarlar soni."""
    settings = get_settings()
    now = datetime.now(UTC)
    async with factory() as session:
        claimed = await repo.claim_due(session, now, now + LEASE, settings.dispatch_batch_size)
        await session.commit()
    for item in claimed:
        async with factory() as session:
            row = await session.get(Notification, item.id, with_for_update=True)
            if row is None or row.status != "sending":
                continue
            try:
                outcome = await deliver(session, channels, row)
            except Exception as exc:
                logger.exception("delivery crashed", extra={"notification_id": str(row.id)})
                outcome = Outcome("retry", error=f"{type(exc).__name__}: {exc}"[:500])
            _apply(row, outcome, datetime.now(UTC), settings.dispatch_max_attempts)
            await session.commit()
    async with factory() as session:
        BACKLOG.set(await repo.backlog(session, datetime.now(UTC)))
    return len(claimed)
