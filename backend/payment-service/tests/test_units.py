"""Sof funksiyalar: fiskal chek taqsimoti, Payme auth, Click summa/imzo, ledger normallashtirish, reestr parseri."""

from __future__ import annotations

import base64

import pytest

from app.services import click, fiscal, ledger, payme, reconciliation
from app.services.errors import InvalidRegistry


def _items(*prices: int) -> list[dict]:
    return [
        {"service_name": f"S{i}", "price_tiyin": p, "mxik_code": "M", "package_code": "P", "vat_percent": 12}
        for i, p in enumerate(prices)
    ]


def test_receipt_items_sum_equals_payment_amount() -> None:
    rows = fiscal.receipt_items(_items(10_000_000, 5_000_000, 333), 3_000_000)
    assert sum(r["price_tiyin"] for r in rows) == 3_000_000 and all(r["price_tiyin"] >= 0 for r in rows)
    assert fiscal.receipt_items([], 100) == []
    full = fiscal.receipt_items(_items(700, 300), 1_000)
    assert [r["price_tiyin"] for r in full] == [700, 300]
    assert not fiscal.missing_codes(full)
    assert fiscal.missing_codes([{**full[0], "code": ""}])
    assert fiscal.vat_tiyin(11_200, 12) == 1_200 and fiscal.vat_tiyin(100, 0) == 0
    detail = fiscal.payme_detail(full)
    assert detail["receipt_type"] == 0 and detail["items"][0]["price"] == 700
    ofd = fiscal.click_ofd_items(full, "123")
    assert ofd[0]["VAT"] == 75 and ofd[0]["CommissionInfo"] == {"TIN": "123"}


def test_payme_basic_auth() -> None:
    def header(value: str) -> str:
        return "Basic " + base64.b64encode(value.encode()).decode()

    assert payme.verify_auth(header("Paycom:payme-test-key"))
    assert not payme.verify_auth(header("Paycom:wrong"))
    assert not payme.verify_auth(header("Other:payme-test-key"))
    assert not payme.verify_auth(None) and not payme.verify_auth("Bearer x") and not payme.verify_auth("Basic ***")


def test_click_amount_and_signature() -> None:
    assert click._amount_tiyin({"amount": "12345.50"}) == 1_234_550
    assert click._amount_tiyin({"amount": "5000"}) == 500_000
    for bad in ("1.005", "abc", "NaN"):
        with pytest.raises(click.ClickError):
            click._amount_tiyin({"amount": bad})
    p = {"click_trans_id": 1, "service_id": 2, "merchant_trans_id": "b", "amount": "10", "action": 0, "sign_time": "t"}
    assert click.expected_sign(p, with_prepare_id=False, secret="s") != click.expected_sign(
        {**p, "merchant_prepare_id": 5}, with_prepare_id=True, secret="s"
    )


def test_ledger_normalize_negative_platform_share() -> None:
    assert ledger._normalize("platform_revenue", 0, -500) == ("platform_revenue", 500, 0)
    assert ledger._normalize("x", -3, 0) == ("x", 0, 3)
    assert ledger.balance_of("client_payments", 10, 3) == 7 and ledger.balance_of("doctor_payable", 3, 10) == 7


def test_registry_parser() -> None:
    rows = reconciliation.parse_registry("﻿id,amount,state,booking_id,extra\nA,100,2,b,z\n")
    assert rows == [reconciliation.ProviderTx("A", 100, 2, "b")]
    with pytest.raises(InvalidRegistry):
        reconciliation.parse_registry("foo,bar\n1,2\n")
    with pytest.raises(InvalidRegistry):
        reconciliation.parse_registry("id,amount,state\nA,x,2\n")
    frm, to = reconciliation.day_window_ms(reconciliation.yesterday())
    assert to - frm == 24 * 3600 * 1000 - 1
