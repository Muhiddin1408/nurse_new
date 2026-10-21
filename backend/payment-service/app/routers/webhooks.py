"""Provayder webhooklari — faqat Payme/Click chaqiradi (JWT yo'q; himoya — imzo/Basic Auth + IP allowlist).
Protokol bo'yicha HAR DOIM HTTP 200, xato javob tanasida (B3)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request

from app.routers.deps import DepsDep, Ip, Session
from app.services import click, payme

router = APIRouter(prefix="/v1/payments", tags=["webhooks"])


@router.post("/payme/webhook")
async def payme_webhook(request: Request, session: Session, deps: DepsDep, ip: Ip) -> dict[str, Any]:
    """Payme Merchant API (JSON-RPC). Basic Auth `Paycom:<PAYME_SECRET_KEY>`."""
    body = await request.body()
    return await payme.webhook(session, deps, body=body, authorization=request.headers.get("authorization"), ip=ip)


async def _click_params(request: Request) -> tuple[dict[str, Any], int]:
    """Click `application/x-www-form-urlencoded` yuboradi (JSON ham qabul qilinadi). Hajm — tanani o'qishdan OLDIN."""
    declared = int(request.headers.get("content-length") or 0)
    if declared > 1024 * 1024:
        return {}, declared
    body = await request.body()
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        return (data if isinstance(data, dict) else {}), len(body)
    return dict(parse_qsl(body.decode(errors="replace"), keep_blank_values=True)), len(body)


@router.post("/click/prepare")
async def click_prepare(request: Request, session: Session, deps: DepsDep, ip: Ip) -> dict[str, Any]:
    params, size = await _click_params(request)
    return await click.webhook(session, deps, action=click.ACTION_PREPARE, params=params, ip=ip, size=size)


@router.post("/click/complete")
async def click_complete(request: Request, session: Session, deps: DepsDep, ip: Ip) -> dict[str, Any]:
    params, size = await _click_params(request)
    return await click.webhook(session, deps, action=click.ACTION_COMPLETE, params=params, ip=ip, size=size)
