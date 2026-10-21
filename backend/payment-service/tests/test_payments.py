"""To'lov sagasi — haqiqiy Postgres + Redis, upstream/provayderlar — tests/fakes.py.

Payme/Click webhooklari haqiqiy protokol (Basic Auth, MD5 imzo) bilan chaqiriladi; eventlar outbox'dan
o'qilib sxemaga tekshiriladi; daftar har ssenariydan keyin balansda (sum debit == sum credit).
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token, sample_data

from app.services import click as click_service
from tests.fakes import DOCTOR, FakeDeps

PAYME_AUTH = {"Authorization": "Basic " + base64.b64encode(b"Paycom:payme-test-key").decode()}


@pytest.fixture
def fakes(api: TestClient, run_handler) -> FakeDeps:
    deps: FakeDeps = run_handler.deps
    api.app.state.deps = deps
    return deps


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class World:
    def __init__(self, api: TestClient, fakes: FakeDeps, db, user_keys, admin_keys) -> None:
        self.api, self.fakes, self.db = api, fakes, db
        self.client_id = uuid.uuid4()
        self.user = bearer(mint_user_token(user_keys, user_id=self.client_id))
        self.admin = bearer(mint_admin_token(admin_keys, permissions=["payments.read", "payments.refund"]))
        self.rpc_id = 0

    def booking(self, **kw: Any) -> uuid.UUID:
        return self.fakes.booking.add(self.client_id, **kw)

    def checkout(self, booking_id: uuid.UUID, provider: str = "payme") -> dict[str, Any]:
        r = self.api.post("/v1/payments", json={"booking_id": str(booking_id), "provider": provider}, headers=self.user)
        assert r.status_code == 200, r.text
        return r.json()

    def payme(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.rpc_id += 1
        r = self.api.post(
            "/v1/payments/payme/webhook",
            json={"id": self.rpc_id, "method": method, "params": params},
            headers=PAYME_AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["jsonrpc"] == "2.0" and body["id"] == self.rpc_id
        return body

    def pay_with_payme(self, booking_id: uuid.UUID, amount: int, tx: str | None = None) -> dict[str, Any]:
        tx = tx or uuid.uuid4().hex[:24]
        self.checkout(booking_id)  # idempotent: ochiq Payme urinishi bo'lsa — o'sha
        account = {"booking_id": str(booking_id)}
        assert "result" in self.payme("CheckPerformTransaction", {"amount": amount, "account": account})
        assert "result" in self.payme("CreateTransaction", {"id": tx, "time": 1, "amount": amount, "account": account})
        result = self.payme("PerformTransaction", {"id": tx})
        assert result["result"]["state"] == 2, result
        return {"tx": tx, **result["result"]}

    def events(self, event_type: str) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT payload FROM outbox_events WHERE event_type = :t ORDER BY id", t=event_type)
        envelopes = [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]
        for e in envelopes:
            assert_event_valid(e)
        return envelopes

    def payment(self, payment_id: str) -> dict[str, Any]:
        r = self.api.get(f"/v1/admin/payments/{payment_id}", headers=self.admin)
        assert r.status_code == 200, r.text
        return r.json()

    def assert_balanced(self) -> dict[str, int]:
        r = self.api.get("/v1/admin/ledger/balances", headers=self.admin).json()
        assert r["trial_balance"] == 0
        return r["balances"]


@pytest.fixture
def world(api: TestClient, fakes: FakeDeps, db, user_keys, admin_keys) -> World:
    return World(api, fakes, db, user_keys, admin_keys)


def click_params(action: int, booking_id: uuid.UUID, amount: str, trans: str, **extra: Any) -> dict[str, Any]:
    p = {
        "click_trans_id": trans,
        "service_id": "777",
        "click_paydoc_id": "9001",
        "merchant_trans_id": str(booking_id),
        "amount": amount,
        "action": str(action),
        "error": "0",
        "error_note": "Success",
        "sign_time": "2026-10-21 10:00:00",
        **extra,
    }
    p["sign_string"] = click_service.expected_sign(p, with_prepare_id=action == 1, secret="click-test-key")
    return p


# --------------------------------------------------------------------------- checkout


def test_checkout_snapshot_idempotent_and_guards(world: World, user_keys) -> None:
    b = world.booking(total=15_000_000, items=[("Konsultatsiya", 10_000_000), ("Analiz", 5_000_000)])
    first = world.checkout(b)
    p = first["payment"]
    assert p["status"] == "created" and p["amount_tiyin"] == 15_000_000 and p["booking_number"].startswith("MB-")
    assert first["checkout_url"].startswith("https://checkout.paycom.uz/")
    decoded = base64.b64decode(first["checkout_url"].rsplit("/", 1)[1]).decode()
    assert decoded == f"m=merchant-test;ac.booking_id={b};a=15000000"
    assert world.checkout(b)["payment"]["id"] == p["id"]  # idempotent
    click = world.checkout(b, "click")
    assert click["payment"]["id"] != p["id"] and "amount=150000.00" in click["checkout_url"]
    assert len(world.events("payment.created")) == 2
    detail = world.payment(p["id"])
    assert sum(i["price_tiyin"] for i in detail["receipt_items"]) == 15_000_000
    assert detail["money"]["total_tiyin"] == 15_000_000

    # begona bron — 404; to'lanmaydigan / klinikada to'lanadigan — 409
    other = world.fakes.booking.add(uuid.uuid4())
    r = world.api.post("/v1/payments", json={"booking_id": str(other), "provider": "payme"}, headers=world.user)
    assert r.status_code == 404
    expired = world.booking(payable=False)
    r = world.api.post("/v1/payments", json={"booking_id": str(expired), "provider": "payme"}, headers=world.user)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_PAYABLE"
    at_clinic = world.booking(prepay=0, mode="at_clinic")
    r = world.api.post("/v1/payments", json={"booking_id": str(at_clinic), "provider": "click"}, headers=world.user)
    assert r.status_code == 409

    r = world.api.post("/v1/payments", json={"booking_id": str(b), "provider": "paypal"}, headers=world.user)
    assert r.status_code == 422

    mine = world.api.get(f"/v1/payments?booking_id={b}", headers=world.user).json()
    assert {x["id"] for x in mine} == {p["id"], click["payment"]["id"]}
    assert world.api.get(f"/v1/payments/{p['id']}", headers=world.user).status_code == 200
    stranger = bearer(mint_user_token(user_keys, user_id=uuid.uuid4()))
    assert world.api.get(f"/v1/payments/{p['id']}", headers=stranger).status_code == 404


# --------------------------------------------------------------------------- Payme


def test_payme_happy_path_captures_and_posts_ledger(world: World) -> None:
    b = world.booking(total=15_000_000, prepay=3_000_000, mode="deposit")  # zaklad 20%
    pid = world.checkout(b)["payment"]["id"]
    account = {"booking_id": str(b)}

    bad = world.payme("CheckPerformTransaction", {"amount": 3_000_001, "account": account})
    assert bad["error"]["code"] == -31001
    unknown = world.payme("CheckPerformTransaction", {"amount": 3_000_000, "account": {"booking_id": "x"}})
    assert unknown["error"]["code"] == -31050
    extra = world.payme("CheckPerformTransaction", {"amount": 3_000_000, "account": {**account, "x": 1}})
    assert extra["error"]["code"] == -31050
    check = world.payme("CheckPerformTransaction", {"amount": 3_000_000, "account": account})["result"]
    assert check["allow"] is True
    assert sum(i["price"] * i["count"] for i in check["detail"]["items"]) == 3_000_000  # chek == to'lov summasi

    created = world.payme("CreateTransaction", {"id": "tx-1", "time": 111, "amount": 3_000_000, "account": account})
    assert created["result"]["state"] == 1 and created["result"]["transaction"] == pid
    again = world.payme("CreateTransaction", {"id": "tx-1", "time": 111, "amount": 3_000_000, "account": account})
    assert again["result"]["create_time"] == created["result"]["create_time"]  # idempotent
    other = world.payme("CreateTransaction", {"id": "tx-2", "time": 112, "amount": 3_000_000, "account": account})
    assert other["error"]["code"] == -31099  # bronda boshqa tranzaksiya jarayonda
    assert world.fakes.booking.extended == [b]  # hold uzaytirildi

    performed = world.payme("PerformTransaction", {"id": "tx-1"})["result"]
    assert performed["state"] == 2 and performed["transaction"] == pid
    assert world.payme("PerformTransaction", {"id": "tx-1"})["result"]["perform_time"] == performed["perform_time"]
    checked = world.payme("CheckTransaction", {"id": "tx-1"})["result"]
    assert checked["state"] == 2 and checked["perform_time"] == performed["perform_time"]
    statement = world.payme("GetStatement", {"from": 0, "to": 2**62})["result"]["transactions"]
    assert [t["id"] for t in statement] == ["tx-1"] and statement[0]["time"] == 111
    assert world.payme("SetFiscalData", {"id": "tx-1", "type": "PERFORM", "fiscal_data": {"qr": "x"}})["result"]

    captured = world.events("payment.captured")
    assert len(captured) == 1 and captured[0]["data"]["amount_tiyin"] == 3_000_000
    assert world.payment(pid)["status"] == "succeeded"
    balances = world.assert_balanced()
    # zaklad: 3 000 000 dan shifokor 84% (12.6/15), platforma 15%, provayder 1%
    assert balances == {
        "client_payments": 3_000_000,
        "platform_revenue": 450_000,
        "doctor_payable": 2_520_000,
        "provider_fees": 30_000,
    }
    doc = world.api.get(f"/v1/admin/ledger/balances?doctor_id={DOCTOR}", headers=world.admin).json()
    assert doc["balances"] == {"doctor_payable": 2_520_000}
    journal = world.api.get(f"/v1/admin/ledger?booking_id={b}", headers=world.admin).json()
    assert [t["kind"] for t in journal] == ["payment_captured"] and len(journal[0]["entries"]) == 4
    logs = world.db.scalar("SELECT count(*) FROM provider_logs WHERE provider = 'payme' AND direction = 'in'")
    assert logs >= 9

    # ikkinchi provayder orqali qayta to'lab bo'lmaydi
    r = world.api.post("/v1/payments", json={"booking_id": str(b), "provider": "click"}, headers=world.user)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ALREADY_PAID"


def test_payme_check_rejects_unpayable_booking_and_cancel_before_perform(world: World) -> None:
    b = world.booking()
    world.checkout(b)
    account = {"booking_id": str(b)}
    world.payme("CreateTransaction", {"id": "tx-c", "time": 1, "amount": 15_000_000, "account": account})
    cancel = world.payme("CancelTransaction", {"id": "tx-c", "reason": 3})["result"]
    assert cancel["state"] == -1
    assert world.payme("CancelTransaction", {"id": "tx-c", "reason": 3})["result"] == cancel  # idempotent
    assert world.payme("PerformTransaction", {"id": "tx-c"})["error"]["code"] == -31008
    failed = world.events("payment.failed")
    assert len(failed) == 1 and failed[0]["data"]["reason"] == "payme_cancelled:3"

    # Yangi urinish ochiladi; bron muddati o'tsa Payme pulni YECHMAYDI (B3)
    world.checkout(b)
    world.fakes.booking.expire(b)
    assert world.payme("CheckPerformTransaction", {"amount": 15_000_000, "account": account})["error"]["code"] == -31099
    assert world.payme("CheckTransaction", {"id": "nope"})["error"]["code"] == -31003
    assert world.payme("Unknown", {})["error"]["code"] == -32601
    assert world.payme("CheckTransaction", {})["error"]["code"] == -32600


def test_payme_transaction_timeout(world: World) -> None:
    b = world.booking()
    world.checkout(b)
    account = {"booking_id": str(b)}
    world.payme("CreateTransaction", {"id": "tx-old", "time": 1, "amount": 15_000_000, "account": account})
    world.db.scalar("UPDATE payments SET provider_create_time = provider_create_time - 13 * 3600 * 1000")
    assert world.payme("PerformTransaction", {"id": "tx-old"})["error"]["code"] == -31008
    checked = world.payme("CheckTransaction", {"id": "tx-old"})["result"]
    assert checked["state"] == -1 and checked["reason"] == 4


def test_payme_reversal_closes_open_refunds_and_reverses_rest(world: World, run_handler, run_async) -> None:
    b = world.booking(total=10_000_000)
    pid = world.checkout(b)["payment"]["id"]
    world.pay_with_payme(b, 10_000_000, tx="tx-rev")
    # Bekor qilish siyosati 50% → refund navbatda; Payme API refund qila olmaydi → manual_required
    run_handler("booking.cancelled", _cancelled(b, world.client_id, refund=5_000_000))
    from app.services import refunds

    assert run_async(lambda f: refunds.process_due(f, world.fakes)) == {"manual_required": 1}  # type: ignore[arg-type]
    # Admin kabinetda butun tranzaksiyani bekor qildi → Payme CancelTransaction(state 2 → −2)
    result = world.payme("CancelTransaction", {"id": "tx-rev", "reason": 5})["result"]
    assert result["state"] == -2
    assert world.payme("CancelTransaction", {"id": "tx-rev", "reason": 5})["result"]["state"] == -2

    refunds_ok = world.events("refund.succeeded")
    assert [e["data"]["amount_tiyin"] for e in refunds_ok] == [5_000_000]
    reversed_ = world.events("payment.reversed")
    assert len(reversed_) == 1 and reversed_[0]["data"]["amount_tiyin"] == 5_000_000
    # shifokor ulushlari yig'indisi to'lovdagi ulushga teng
    shares = refunds_ok[0]["data"]["doctor_share_tiyin"] + reversed_[0]["data"]["doctor_share_tiyin"]
    assert shares == 8_400_000
    assert world.payment(pid)["status"] == "reversed"
    balances = world.assert_balanced()
    assert balances["client_payments"] == 0 and balances["doctor_payable"] == 0
    assert balances["platform_revenue"] == -100_000  # provayder komissiyasini platforma ko'tardi
    assert world.payme("CheckTransaction", {"id": "tx-rev"})["result"]["state"] == -2


# --------------------------------------------------------------------------- Click


def test_click_prepare_complete_and_fiscal(world: World, run_async) -> None:
    b = world.booking(total=1_234_550)
    pid = world.checkout(b, "click")["payment"]["id"]
    bad = click_params(0, b, "12345.50", "c-1") | {"sign_string": "x"}
    assert world.api.post("/v1/payments/click/prepare", data=bad).json()["error"] == -1
    wrong_amount = click_params(0, b, "12345.51", "c-1")
    assert world.api.post("/v1/payments/click/prepare", data=wrong_amount).json()["error"] == -2
    prep = world.api.post("/v1/payments/click/prepare", data=click_params(0, b, "12345.50", "c-1")).json()
    assert prep["error"] == 0 and prep["merchant_prepare_id"] > 0
    again = world.api.post("/v1/payments/click/prepare", data=click_params(0, b, "12345.50", "c-1")).json()
    assert again["merchant_prepare_id"] == prep["merchant_prepare_id"]

    wrong_prep = click_params(1, b, "12345.50", "c-1", merchant_prepare_id="1")
    assert world.api.post("/v1/payments/click/complete", data=wrong_prep).json()["error"] == -6
    done = click_params(1, b, "12345.50", "c-1", merchant_prepare_id=str(prep["merchant_prepare_id"]))
    complete = world.api.post("/v1/payments/click/complete", data=done).json()
    assert complete["error"] == 0 and complete["merchant_confirm_id"] > 0
    assert (
        world.api.post("/v1/payments/click/complete", data=done).json()["merchant_confirm_id"]
        == complete["merchant_confirm_id"]
    )
    assert world.payment(pid)["status"] == "succeeded"
    assert len(world.events("payment.captured")) == 1
    # ikkinchi Prepare (boshqa trans) — allaqachon to'langan
    assert (
        world.api.post("/v1/payments/click/prepare", data=click_params(0, b, "12345.50", "c-2")).json()["error"] == -4
    )
    world.assert_balanced()

    from app.services import maintenance

    assert run_async(lambda f: maintenance.submit_click_fiscal(f, world.fakes)) == {"submitted": 1, "failed": 0}  # type: ignore[arg-type]
    sent = world.fakes.click.fiscal[0]
    assert sent["paydoc_id"] == "9001" and sum(i["Price"] for i in sent["items"]) == 1_234_550
    assert sent["items"][0]["CommissionInfo"] == {"TIN": "123456789"}
    assert run_async(lambda f: maintenance.submit_click_fiscal(f, world.fakes)) == {"submitted": 0, "failed": 0}  # type: ignore[arg-type]


def test_click_cancelled_by_provider(world: World) -> None:
    b = world.booking(total=500_000)
    pid = world.checkout(b, "click")["payment"]["id"]
    prep = world.api.post("/v1/payments/click/prepare", data=click_params(0, b, "5000", "c-9")).json()
    p = click_params(1, b, "5000", "c-9", merchant_prepare_id=str(prep["merchant_prepare_id"]), error="-5017")
    assert world.api.post("/v1/payments/click/complete", data=p).json()["error"] == -9
    assert world.payment(pid)["status"] == "cancelled"
    assert world.events("payment.failed")[0]["data"]["reason"] == "click_error:-5017"
    assert world.api.post("/v1/payments/click/prepare", data=click_params(0, b, "5000", "c-9")).json()["error"] == -9


# --------------------------------------------------------------------------- saga va refundlar


def _cancelled(booking_id: uuid.UUID, client_id: uuid.UUID, *, refund: int) -> dict[str, Any]:
    return sample_data(
        "booking.cancelled",
        booking_id=str(booking_id),
        client_id=str(client_id),
        doctor_id=str(DOCTOR),
        cancelled_by="client",
        reason_code="client_cancelled",
        refund_tiyin=refund,
        previous_status="confirmed",
    )


def test_confirmation_failed_compensation_refunds_in_full(world: World, run_handler, run_async) -> None:
    """Saga: pul yechildi → booking tasdiqlay olmadi → to'liq refund (Click reversal), takroriy event — bitta refund."""
    b = world.booking(total=2_000_000)
    pid = world.checkout(b, "click")["payment"]["id"]
    prep = world.api.post("/v1/payments/click/prepare", data=click_params(0, b, "20000", "c-s")).json()
    done = click_params(1, b, "20000", "c-s", merchant_prepare_id=str(prep["merchant_prepare_id"]))
    assert world.api.post("/v1/payments/click/complete", data=done).json()["error"] == 0

    event = sample_data(
        "booking.confirmation_failed",
        booking_id=str(b),
        client_id=str(world.client_id),
        payment_id=pid,
        amount_tiyin=2_000_000,
        reason="booking_expired",
    )
    run_handler("booking.confirmation_failed", event)
    run_handler("booking.confirmation_failed", event)  # boshqa event_id bilan takror — baribir bitta refund
    detail = world.payment(pid)
    assert detail["needs_refund"] is True and len(detail["refunds"]) == 1
    assert detail["refunds"][0]["source"] == "confirmation_failed" and detail["refunds"][0]["amount_tiyin"] == 2_000_000

    from app.services import refunds

    assert run_async(lambda f: refunds.process_due(f, world.fakes)) == {"succeeded": 1}  # type: ignore[arg-type]
    assert world.fakes.click.calls == [{"paydoc_id": "9001", "amount_tiyin": 2_000_000}]
    ok = world.events("refund.succeeded")
    assert len(ok) == 1 and ok[0]["data"]["source"] == "confirmation_failed"
    assert ok[0]["data"]["doctor_share_tiyin"] == 1_680_000
    assert world.payment(pid)["status"] == "refunded"
    balances = world.assert_balanced()
    assert balances["client_payments"] == 0 and balances["doctor_payable"] == 0


def test_partial_refunds_retry_failure_and_admin_flow(world: World, run_handler, run_async) -> None:
    b = world.booking(total=10_000_000)
    pid = world.checkout(b, "click")["payment"]["id"]
    prep = world.api.post("/v1/payments/click/prepare", data=click_params(0, b, "100000", "c-p")).json()
    done = click_params(1, b, "100000", "c-p", merchant_prepare_id=str(prep["merchant_prepare_id"]))
    world.api.post("/v1/payments/click/complete", data=done)

    # Click qisman refund qila olmaydi — haqiqiy adapterda ManualRefundRequired; bu yerda xato → retry → failed
    run_handler(
        "booking.no_show",
        sample_data(
            "booking.no_show",
            booking_id=str(b),
            client_id=str(world.client_id),
            doctor_id=str(DOCTOR),
            no_show_by="doctor",
            marked_by="admin",
            refund_tiyin=4_000_000,
        ),
    )
    world.fakes.click.outcome = "error"
    from app.services import refunds

    for _ in range(5):
        world.db.scalar("UPDATE refunds SET next_attempt_at = now() WHERE status = 'pending'")
        run_async(lambda f: refunds.process_due(f, world.fakes))  # type: ignore[arg-type]
    listed = world.api.get(f"/v1/admin/refunds?booking_id={b}", headers=world.admin).json()
    assert listed[0]["status"] == "failed" and listed[0]["attempts"] == 5
    failed = world.events("refund.failed")
    assert len(failed) == 1 and failed[0]["data"]["attempts"] == 5

    # admin qayta navbatga qo'ydi → muvaffaqiyat
    world.fakes.click.outcome = "ok"
    r = world.api.post(f"/v1/admin/refunds/{listed[0]['id']}/retry", headers=world.admin)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert run_async(lambda f: refunds.process_due(f, world.fakes)) == {"succeeded": 1}  # type: ignore[arg-type]
    assert world.payment(pid)["status"] == "partially_refunded"

    # admin qo'lda refund: Idempotency-Key bilan; qoldiqdan oshmaydi
    key = {"Idempotency-Key": "admin-refund-0001"}
    body = {"payment_id": pid, "amount_tiyin": 99_000_000, "reason": "goodwill"}
    r = world.api.post("/v1/admin/refunds", json=body, headers=world.admin | key)
    assert r.status_code == 200 and r.json()["amount_tiyin"] == 6_000_000 and r.json()["source"] == "admin"
    assert world.api.post("/v1/admin/refunds", json=body, headers=world.admin | key).json()["id"] == r.json()["id"]
    assert world.api.post("/v1/admin/refunds", json=body, headers=world.admin).status_code == 422  # kalitsiz
    r2 = world.api.post("/v1/admin/refunds", json=body, headers=world.admin | {"Idempotency-Key": "admin-refund-0002"})
    assert r2.status_code == 409  # qoldiq yo'q
    world.fakes.click.outcome = "manual"
    assert run_async(lambda f: refunds.process_due(f, world.fakes)) == {"manual_required": 1}  # type: ignore[arg-type]
    complete = world.api.post(
        f"/v1/admin/refunds/{r.json()['id']}/complete",
        json={"external_ref": "CABINET-77", "note": "kabinetda"},
        headers=world.admin,
    )
    assert complete.status_code == 200 and complete.json()["status"] == "succeeded"
    again = world.api.post(
        f"/v1/admin/refunds/{r.json()['id']}/complete", json={"external_ref": "X"}, headers=world.admin
    )
    assert again.status_code == 409
    assert world.payment(pid)["status"] == "refunded"
    audit = {e["data"]["action"] for e in world.events("audit.recorded")}
    assert {"refund.request", "refund.complete_manual", "refund.retry"} <= audit
    shares = [e["data"]["doctor_share_tiyin"] for e in world.events("refund.succeeded")]
    assert sum(shares) == 8_400_000  # to'lovdagi shifokor ulushidan oshmaydi, oxirgisi aniq qoldiq
    world.assert_balanced()


def test_cancel_without_payment_and_dispute_not_liable(world: World, run_handler) -> None:
    # at_clinic / to'lanmagan bron bekor qilindi — refund yo'q
    run_handler("booking.cancelled", _cancelled(uuid.uuid4(), world.client_id, refund=1_000))
    run_handler("booking.cancelled", _cancelled(uuid.uuid4(), world.client_id, refund=0))
    assert world.db.scalar("SELECT count(*) FROM refunds") == 0

    b = world.booking(total=1_000_000)
    world.pay_with_payme(b, 1_000_000)
    dispute = sample_data(
        "dispute.resolved",
        booking_id=str(b),
        dispute_id=str(uuid.uuid4()),
        resolution="refund_partial",
        refund_tiyin=300_000,
        doctor_liable=False,
    )
    run_handler("dispute.resolved", dispute)
    row = world.db.all("SELECT amount_tiyin, doctor_share_tiyin, source FROM refunds")
    assert [tuple(r) for r in row] == [(300_000, 0, "dispute")]


def test_payout_paid_closes_doctor_payable(world: World, run_handler) -> None:
    b = world.booking(total=1_000_000)
    world.pay_with_payme(b, 1_000_000)
    payout = sample_data("payout.paid", doctor_id=str(DOCTOR), net_tiyin=840_000, payout_id=str(uuid.uuid4()))
    run_handler("payout.paid", payout)
    run_handler("payout.paid", payout)  # idempotent
    balances = world.assert_balanced()
    assert balances["doctor_payable"] == 0 and balances["client_payments"] == 160_000


def test_settings_changed_invalidates_cache(world: World, run_handler) -> None:
    run_handler("admin.settings_changed", sample_data("admin.settings_changed"))
    assert world.fakes.settings.invalidated == 1


# --------------------------------------------------------------------------- reconciliation va texnik ishlar


def test_reconciliation_registry_internal_and_resolve(world: World, run_async) -> None:
    from datetime import UTC, datetime

    from app.services import reconciliation

    b = world.booking(total=1_000_000)
    paid = world.pay_with_payme(b, 1_000_000, tx="tx-rec")
    day = datetime.fromtimestamp(paid["perform_time"] / 1000, tz=UTC).astimezone(reconciliation.LOCAL_TZ).date()
    csv_text = "id,amount,state,booking_id\ntx-rec,1000001,2,x\ntx-ghost,500000,2,y\ntx-zero,100,-1,z\n"
    r = world.api.post(
        f"/v1/admin/reconciliation/registry?provider=payme&day={day}",
        content=csv_text.encode(),
        headers=world.admin | {"Content-Type": "text/csv"},
    )
    assert r.status_code == 200, r.text
    assert sorted(d["type"] for d in r.json()) == ["amount_mismatch", "provider_only"]
    again = world.api.post(
        f"/v1/admin/reconciliation/registry?provider=payme&day={day}", content=csv_text.encode(), headers=world.admin
    )
    assert again.json() == []  # shu kun uchun dublikat yo'q
    bad = world.api.post(
        f"/v1/admin/reconciliation/registry?provider=payme&day={day}", content=b"a,b\n1,2", headers=world.admin
    )
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "INVALID_REGISTRY"
    found = world.events("payment.discrepancy_found")
    assert {e["data"]["type"] for e in found} == {"amount_mismatch", "provider_only"}

    # ichki: daftarga tushmagan to'lov
    world.db.scalar("DELETE FROM ledger_entries")
    world.db.scalar("DELETE FROM ledger_transactions")

    async def internal(factory) -> list[str]:  # type: ignore[no-untyped-def]
        async with factory() as session, session.begin():
            return [d.type for d in await reconciliation.reconcile_internal(session, day)]

    assert run_async(internal) == ["missing_ledger"]
    assert run_async(internal) == []

    listed = world.api.get("/v1/admin/reconciliation", headers=world.admin).json()
    assert len(listed) == 3
    target = next(d for d in listed if d["type"] == "provider_only")
    r = world.api.post(
        f"/v1/admin/reconciliation/{target['id']}/resolve",
        json={"note": "Payme bilan hal qilindi"},
        headers=world.admin,
    )
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert len(world.api.get("/v1/admin/reconciliation", headers=world.admin).json()) == 2
    assert (
        world.api.post(
            f"/v1/admin/reconciliation/{uuid.uuid4()}/resolve", json={"note": "x"}, headers=world.admin
        ).status_code
        == 404
    )


def test_expire_stale_and_purge_logs(world: World, run_async) -> None:
    from app.services import maintenance

    b = world.booking()
    pid = world.checkout(b)["payment"]["id"]
    world.db.scalar("UPDATE payments SET created_at = now() - interval '13 hours'")
    world.db.scalar(
        "INSERT INTO provider_logs (provider, direction, operation, request, response, error, duration_ms, created_at) "
        "VALUES ('payme', 'in', 'x', '{}', '{}', '', 0, now() - interval '400 days')"
    )
    assert run_async(maintenance.expire_stale) == 1
    assert world.payment(pid)["status"] == "expired"
    assert world.events("payment.failed")[0]["data"]["reason"] == "expired"
    assert run_async(maintenance.purge_logs) == 1
