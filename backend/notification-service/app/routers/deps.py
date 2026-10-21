from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.telegram import TelegramClient
from app.core.database import get_session

Session = Annotated[AsyncSession, Depends(get_session)]


def get_telegram(request: Request) -> TelegramClient:
    telegram: TelegramClient = request.app.state.telegram
    return telegram


Telegram = Annotated[TelegramClient, Depends(get_telegram)]
