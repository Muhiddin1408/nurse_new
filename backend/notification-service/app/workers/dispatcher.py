"""Xabarlarni yuborish va davriy vazifalar — alohida konteyner: `python -m app.workers.dispatcher`.

- har `DISPATCH_INTERVAL_SECONDS` (2 s): vaqti kelgan xabarlar (darhol va rejalashtirilgan eslatmalar);
- har kuni 07:00 (Asia/Tashkent): shifokorlarning kunlik digest'i;
- har kuni: 90 kundan eski yakunlangan xabarlar jurnaldan o'chiriladi.
Bir nechta nusxa parallel ishlashi mumkin (SKIP LOCKED + dedupe_key).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from medbron_common.db import transaction
from medbron_common.logging import configure_logging
from medbron_common.sms import RedisTokenStore, create_sms_client
from medbron_common.workers import DailyAt, Every, PeriodicJob, run_periodic, run_worker

from app.channels.push import create_push_client
from app.channels.telegram import TelegramClient
from app.core.config import get_settings
from app.core.database import get_engine, get_session_factory
from app.core.redis import get_redis
from app.repositories import notifications as repo
from app.services import digest
from app.services.dispatcher import Channels, dispatch_once

LOG_RETENTION = timedelta(days=90)


def build_channels() -> Channels:
    settings = get_settings()
    redis = get_redis() if settings.redis_url else None
    return Channels(
        sms=create_sms_client(
            settings.sms_config(),
            environment=settings.environment,
            token_store=RedisTokenStore(redis) if redis is not None else None,
        ),
        push=create_push_client(
            settings.push_provider,
            settings.fcm_credentials_file,
            settings.fcm_project_id,
            settings.fcm_credentials_json.get_secret_value() if settings.fcm_credentials_json else None,
        ),
        telegram=TelegramClient(
            settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else None
        ),
        ops_chat_id=settings.ops_telegram_chat_id,
    )


async def purge() -> int:
    async with get_session_factory()() as session, transaction(session):
        return await repo.purge_old(session, datetime.now(UTC) - LOG_RETENTION)


async def daily_digest() -> int:
    async with get_session_factory()() as session:
        return await digest.enqueue_daily(session)


async def main(stop: asyncio.Event) -> None:
    settings = get_settings()
    configure_logging(f"{settings.service_name}-dispatcher", settings.log_level)
    channels = build_channels()
    factory = get_session_factory()

    async def dispatch() -> int:
        total = 0
        # Navbat to'lgan bo'lsa — partiyalar ketma-ket, keyingi intervalni kutmasdan
        while not stop.is_set():
            handled = await dispatch_once(factory, channels)
            total += handled
            if handled < settings.dispatch_batch_size:
                return total
        return total

    try:
        await run_periodic(
            stop,
            [
                PeriodicJob(
                    "notifications-dispatch", Every(settings.dispatch_interval_seconds), dispatch, run_on_start=True
                ),
                PeriodicJob("doctor-digest", DailyAt(settings.digest_hour, 0, "Asia/Tashkent"), daily_digest),
                PeriodicJob("notifications-purge", Every(24 * 3600), purge),
            ],
        )
    finally:
        await channels.sms.aclose()
        await channels.push.aclose()
        await channels.telegram.aclose()
        if settings.redis_url:
            await get_redis().aclose()
        await get_engine().dispose()


if __name__ == "__main__":
    run_worker(main)
