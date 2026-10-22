from __future__ import annotations

import httpx
import pytest

from medbron_common.http_client import ServiceClient, UpstreamUnavailable
from medbron_common.money import Share, adjust, bp_of, percent_of, prorate, share_of, tiyin_to_som
from medbron_common.settings_client import PlatformSettingsClient


def test_money_half_up() -> None:
    assert percent_of(15_000_000, 20) == 3_000_000
    assert percent_of(5, 50) == 3  # 2.5 -> 3
    assert percent_of(1, 50) == 1 and percent_of(1, 49) == 0
    assert bp_of(15_000_000, 1500) == 2_250_000 and bp_of(15_000_000, 100) == 150_000
    assert bp_of(-50, 5000) == -25 and bp_of(-5, 5000) == -3
    assert adjust(10_000_000, -15) == 8_500_000 and adjust(10_000_000, 20) == 12_000_000
    assert adjust(15_000_033, 0) == 15_000_033 and adjust(100, -100) == 0
    assert adjust(3, -50) == 2  # 1.5 -> 2
    assert tiyin_to_som(12_500_000) == "125 000" and tiyin_to_som(12_500_050) == "125 000.50"
    with pytest.raises(ValueError):
        from medbron_common.money import _div_half_up

        _div_half_up(1, 0)


def test_share_of_full_partial_and_rounding() -> None:
    money = {"total_tiyin": 15_000_000, "platform_fee_tiyin": 2_250_000, "provider_fee_tiyin": 150_000,
             "doctor_payout_tiyin": 12_600_000}
    assert share_of(money, 15_000_000) == Share(12_600_000, 2_250_000, 150_000)
    deposit = share_of(money, 3_000_000)  # 20% zaklad
    assert deposit == Share(2_520_000, 450_000, 30_000)
    odd = share_of(money, 1)
    assert odd.doctor_tiyin + odd.platform_tiyin + odd.provider_tiyin == 1
    # Chegirmani platforma ko'targan: platform ulushi manfiy, yig'indi baribir summaga teng
    promo = {"total_tiyin": 10_000_000, "platform_fee_tiyin": -500_000, "provider_fee_tiyin": 100_000,
             "doctor_payout_tiyin": 10_400_000}
    half = share_of(promo, 5_000_000)
    assert half == Share(5_200_000, -250_000, 50_000)
    assert prorate(7, 1, 0) == 0 and prorate(10, 1, 3) == 3


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


async def test_settings_cache_etag_and_fallback() -> None:
    state = {"version": 1, "down": False, "calls": 0, "etags": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        state["etags"].append(request.headers.get("if-none-match"))
        if state["down"]:
            return httpx.Response(503)
        if request.headers.get("if-none-match") == f'"v{state["version"]}"':
            return httpx.Response(304)
        return httpx.Response(200, json={"version": state["version"], "values": {"schedule": {"hold_ttl_minutes": 10}}})

    http = ServiceClient("admin-service", "http://admin", caller="booking-service", token="t",
                         transport=httpx.MockTransport(handler), backoff_base=0, max_attempts=1)  # fmt: skip
    redis = FakeRedis()
    client = PlatformSettingsClient(http, redis)
    assert (await client.get())["schedule"]["hold_ttl_minutes"] == 10
    await client.get()
    assert state["calls"] == 1  # kesh

    await client.invalidate()
    await client.get()
    assert state["calls"] == 2 and state["etags"][-1] == '"v1"'  # 304 — o'zgarmagan

    await client.invalidate()
    state["down"] = True
    assert (await client.snapshot())["version"] == 1  # admin yiqildi — oxirgi ma'lum

    cold = PlatformSettingsClient(http, FakeRedis())
    with pytest.raises(UpstreamUnavailable):
        await cold.get()
    await http.aclose()
