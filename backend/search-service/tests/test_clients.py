"""catalog eksport client'i: keyset sahifalash, sarlavhalar (contracts/openapi/catalog-service.internal.yaml)."""

from __future__ import annotations

import httpx

from medbron_common.http_client import ServiceClient

from app.clients.catalog import CatalogClient


async def test_catalog_export_paging() -> None:
    client = CatalogClient("http://x")
    seen: list[httpx.Request] = []
    pages = {None: {"items": [{"doctor_id": "a"}, {"doctor_id": "b"}], "next_after": "b"},
             "b": {"items": [{"doctor_id": "c"}], "next_after": None}}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/specializations"):
            return httpx.Response(200, json={"items": [{"id": "s", "name": "N", "name_ru": "", "slug": "n"}]})
        return httpx.Response(200, json=pages[request.url.params.get("after")])

    client.http = ServiceClient(
        "catalog-service", "http://upstream", caller="search-service", token="tok",
        transport=httpx.MockTransport(handler), backoff_base=0,
    )  # fmt: skip
    got = [[d["doctor_id"] for d in page] async for page in client.doctors(2)]
    assert got == [["a", "b"], ["c"]]
    assert seen[0].url.path == "/internal/v1/export/doctors" and seen[0].url.params["limit"] == "2"
    assert seen[1].url.params["after"] == "b"
    assert seen[0].headers["x-caller"] == "search-service" and seen[0].headers["x-internal-token"] == "tok"
    assert [p async for p in client.clinics(5)] == [[{"doctor_id": "a"}, {"doctor_id": "b"}], [{"doctor_id": "c"}]]
    assert (await client.specializations())[0]["slug"] == "n"
    await client.aclose()
