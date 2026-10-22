"""ClickHouse HTTP interfeysi (`:8123`) — `httpx` bilan, qo'shimcha drayversiz.

    ch = ClickHouse.from_settings()
    await ch.insert("events_raw", rows)                                  # JSONEachRow
    rows = await ch.select("SELECT ... WHERE day >= {frm:Date}", {"frm": date(...)})

So'rov parametrlari — ClickHouse server tomonidagi `{name:Type}` + `param_<name>` (SQL injection yo'q,
qiymatlar so'rov matniga qo'shilmaydi). Xato — `ClickHouseError` (javob matni bilan).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings, get_settings


class ClickHouseError(Exception):
    pass


def _param(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if isinstance(value, date | UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(f"'{v}'" for v in value) + "]"
    return str(value)


class ClickHouse:
    def __init__(
        self,
        base_url: str,
        *,
        user: str,
        password: str,
        database: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.database = database
        self.http = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-ClickHouse-User": user, "X-ClickHouse-Key": password},
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ClickHouse:
        s = settings or get_settings()
        password = s.clickhouse_password.get_secret_value() if s.clickhouse_password else ""
        return cls(
            s.clickhouse_url,
            user=s.clickhouse_user,
            password=password,
            database=s.clickhouse_database,
            timeout=s.clickhouse_timeout_seconds,
        )

    async def _post(
        self, query: str, *, body: bytes | None = None, params: Mapping[str, Any] | None = None, database: bool = True
    ) -> httpx.Response:
        query_params: dict[str, str] = {"query": query} if body is not None else {}
        if database:
            query_params["database"] = self.database
        query_params.update({f"param_{k}": _param(v) for k, v in (params or {}).items()})
        try:
            response = await self.http.post("/", params=query_params, content=body if body is not None else query)
        except httpx.HTTPError as exc:
            raise ClickHouseError(f"clickhouse tarmoq xatosi: {exc}") from exc
        if response.status_code != 200:
            raise ClickHouseError(f"clickhouse {response.status_code}: {response.text[:500]}")
        return response

    async def command(self, sql: str, *, database: bool = True) -> None:
        await self._post(sql, database=database)

    async def insert(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        body = "\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":"), default=str) for r in rows)
        await self._post(f"INSERT INTO {table} FORMAT JSONEachRow", body=body.encode())

    async def select(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        response = await self._post(f"{sql}\nFORMAT JSON", params=params)
        data: list[dict[str, Any]] = response.json()["data"]
        return data

    async def ping(self) -> None:
        try:
            response = await self.http.get("/ping")
        except httpx.HTTPError as exc:
            raise ClickHouseError(f"clickhouse tarmoq xatosi: {exc}") from exc
        if response.status_code != 200:
            raise ClickHouseError(f"clickhouse ping {response.status_code}")
        await self.select("SELECT 1 AS ok")  # auth va baza mavjudligi

    async def aclose(self) -> None:
        await self.http.aclose()
