"""docs/SERVICE_REGISTRY.md ni o'qish (check-contracts.py, check-registry.py, new-service.sh uchun)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "docs" / "SERVICE_REGISTRY.md"
EVENT_RE = re.compile(r"`([a-z_]+\.[a-z_]+)`")


@dataclass
class Service:
    nn: str
    name: str
    lang: str
    internal_port: str
    host_port: str
    pg_port: str
    redis_port: str
    publishes_cell: str
    consumes_cell: str
    calls: list[str] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.name.removesuffix("-service")

    def events_in(self, cell: str) -> set[str] | None:
        """Katakdagi aniq event ro'yxati; umumlashtirilgan bo'lsa (masalan `booking.*`, `#`) — None."""
        if "*" in cell or "#" in cell or " ta" in cell:
            return None
        return set(EVENT_RE.findall(cell))


def _short_to_name(short: str, names: set[str]) -> str:
    for candidate in (short, f"{short}-service", short.replace("ws-gw", "ws-gateway")):
        if candidate in names:
            return candidate
    raise ValueError(f"reestrda noma'lum servis: {short!r}")


def load_services() -> dict[str, Service]:
    text = REGISTRY.read_text()
    section = text.split("## 2. Asosiy jadval", 1)[1].split("\n## ", 1)[0]
    services: dict[str, Service] = {}
    raw_calls: dict[str, str] = {}
    for line in section.splitlines():
        if not re.match(r"^\| \d\d \|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        nn, name, lang, internal, host, pg, redis, pubs, cons, calls = cells[:10]
        lang = lang.replace("*", "")
        services[name] = Service(nn, name, lang, internal, host, pg, redis, pubs, cons)
        raw_calls[name] = calls
    names = set(services)
    for name, cell in raw_calls.items():
        if cell.strip() in {"—", "-", ""}:
            continue
        services[name].calls = [_short_to_name(s.strip(), names) for s in cell.split(",") if s.strip()]
    return services
