"""/v1/notifications — sozlamalar va push qurilmalar; /v1/telegram — bog'lash havolasi va webhook."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, status

from medbron_common.db import transaction
from medbron_common.errors import NotFound

from app.core.config import get_settings
from app.core.security import CurrentUser, get_current_user
from app.models import Contact
from app.routers.deps import Session, Telegram
from app.schemas.notifications import DeviceDelete, DeviceIn, LinkTokenOut, PreferencesOut, PreferencesUpdate
from app.services import contacts

router = APIRouter(tags=["notifications"])

User = Annotated[CurrentUser, Depends(get_current_user)]


def _prefs(contact: Contact) -> PreferencesOut:
    return PreferencesOut(
        push_enabled=contact.push_enabled,
        telegram_enabled=contact.telegram_enabled,
        sms_enabled=contact.sms_enabled,
        marketing_opt_in=contact.marketing_opt_in,
        telegram_linked=contact.telegram_chat_id is not None,
    )


@router.get("/v1/notifications/preferences", response_model=PreferencesOut)
async def get_preferences(user: User, session: Session) -> PreferencesOut:
    async with transaction(session):
        contact = await contacts.preferences(session, user.user_id)
    return _prefs(contact)


@router.patch("/v1/notifications/preferences", response_model=PreferencesOut)
async def patch_preferences(body: PreferencesUpdate, user: User, session: Session) -> PreferencesOut:
    """Tranzaksion xabarlar (bron, to'lov) baribir yetkaziladi: bepul kanallar ishlamasa SMS
    o'chirilgan bo'lsa ham ketadi. Marketing — faqat `marketing_opt_in` bilan va SMS'siz."""
    async with transaction(session):
        contact = await contacts.update_preferences(session, user.user_id, body.model_dump(exclude_none=True))
    return _prefs(contact)


@router.post("/v1/notifications/devices", status_code=status.HTTP_204_NO_CONTENT)
async def register_device(body: DeviceIn, user: User, session: Session) -> Response:
    """Har ilova ochilganda (FCM token o'zgarishi mumkin)."""
    async with transaction(session):
        await contacts.register_device(session, user.user_id, body.token, body.platform)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/v1/notifications/devices", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(body: DeviceDelete, user: User, session: Session) -> Response:
    """Logout'da."""
    async with transaction(session):
        await contacts.remove_device(session, user.user_id, body.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/v1/telegram/link-token", response_model=LinkTokenOut)
async def link_token(user: User, session: Session) -> LinkTokenOut:
    """Telegram'ni ulash havolasi (SMS o'rniga bepul kanal) — mijoz ham, shifokor ham."""
    async with transaction(session):
        contact = await contacts.preferences(session, user.user_id)
    return LinkTokenOut(
        url=contacts.link_url(user.user_id),
        expires_in=get_settings().telegram_link_ttl_seconds,
        linked=contact.telegram_chat_id is not None,
    )


@router.post("/v1/telegram/webhook", status_code=status.HTTP_200_OK)
async def telegram_webhook(
    request: Request,
    session: Session,
    telegram: Telegram,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Telegram `setWebhook(secret_token=...)` bilan. Sir sozlanmagan bo'lsa — endpoint yopiq (404, fail-closed)."""
    secret = get_settings().telegram_webhook_secret
    if secret is None or not hmac.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(), secret.get_secret_value().encode()
    ):
        raise NotFound()
    update = await request.json()
    async with transaction(session):
        await contacts.handle_update(session, telegram, update if isinstance(update, dict) else {})
    return {"ok": True}
