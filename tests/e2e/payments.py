"""E2E: provayder "kabineti" — Payme JSON-RPC va Click SHOP API webhooklarini Kong orqali, haqiqiy protokol
(Basic Auth / MD5 imzo) bilan chaqiradi. Kalitlar — lokal payment-service `.env` dan (scripts/e2e.sh eksport qiladi).
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import os
import uuid
from typing import Any

import httpx

PAYME_KEY = os.environ.get("E2E_PAYME_KEY", "")
CLICK_KEY = os.environ.get("E2E_CLICK_KEY", "")
CLICK_SERVICE_ID = "777"  # scripts/dev-env.py LOCAL_OVERRIDES
_rpc_ids = itertools.count(1)


def checkout(http: httpx.Client, h: dict[str, str], booking_id: str, provider: str = "payme") -> dict[str, Any]:
    r = http.post("/v1/payments", headers=h, json={"booking_id": booking_id, "provider": provider})
    assert r.status_code == 200, r.text
    return dict(r.json())


def payme(http: httpx.Client, method: str, params: dict[str, Any]) -> dict[str, Any]:
    assert PAYME_KEY, "E2E_PAYME_KEY yo'q — scripts/e2e.sh orqali ishga tushiring"
    auth = "Basic " + base64.b64encode(f"Paycom:{PAYME_KEY}".encode()).decode()
    rpc_id = next(_rpc_ids)
    r = http.post(
        "/v1/payments/payme/webhook",
        json={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params},
        headers={"Authorization": auth},
    )
    assert r.status_code == 200, r.text  # protokol: xatoda ham 200
    body = dict(r.json())
    assert body["id"] == rpc_id
    return body


def payme_open(http: httpx.Client, booking_id: str, amount: int) -> str:
    """CheckPerform + Create (pul hali yechilmagan). Qaytaradi: Payme tranzaksiya id."""
    tx = uuid.uuid4().hex[:24]
    account = {"booking_id": booking_id}
    check = payme(http, "CheckPerformTransaction", {"amount": amount, "account": account})
    assert check["result"]["allow"] is True, check
    assert sum(i["price"] * i["count"] for i in check["result"]["detail"]["items"]) == amount  # fiskal chek
    created = payme(http, "CreateTransaction", {"id": tx, "time": 1, "amount": amount, "account": account})
    assert created["result"]["state"] == 1, created
    return tx


def payme_pay(http: httpx.Client, booking_id: str, amount: int) -> str:
    tx = payme_open(http, booking_id, amount)
    performed = payme(http, "PerformTransaction", {"id": tx})
    assert performed["result"]["state"] == 2, performed
    return tx


def _click(action: int, booking_id: str, amount_tiyin: int, trans: str, **extra: Any) -> dict[str, Any]:
    assert CLICK_KEY, "E2E_CLICK_KEY yo'q — scripts/e2e.sh orqali ishga tushiring"
    som, tiyin = divmod(amount_tiyin, 100)
    p: dict[str, Any] = {
        "click_trans_id": trans,
        "service_id": CLICK_SERVICE_ID,
        "click_paydoc_id": trans,
        "merchant_trans_id": booking_id,
        "amount": f"{som}.{tiyin:02d}",
        "action": str(action),
        "error": "0",
        "error_note": "Success",
        "sign_time": "2026-10-21 10:00:00",
        **extra,
    }
    parts = [p["click_trans_id"], p["service_id"], CLICK_KEY, p["merchant_trans_id"]]
    if action == 1:
        parts.append(str(p["merchant_prepare_id"]))
    parts += [p["amount"], p["action"], p["sign_time"]]
    p["sign_string"] = hashlib.md5("".join(parts).encode()).hexdigest()  # noqa: S324 — Click protokoli
    return p


def click_pay(http: httpx.Client, booking_id: str, amount_tiyin: int) -> None:
    trans = str(uuid.uuid4().int % 10**12)
    prep = http.post("/v1/payments/click/prepare", data=_click(0, booking_id, amount_tiyin, trans)).json()
    assert prep["error"] == 0, prep
    done = _click(1, booking_id, amount_tiyin, trans, merchant_prepare_id=prep["merchant_prepare_id"])
    complete = http.post("/v1/payments/click/complete", data=done).json()
    assert complete["error"] == 0, complete
