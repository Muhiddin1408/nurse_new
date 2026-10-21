"""Event payload'ini `contracts/events/*.json` sxemalariga qarshi tekshirish.

Sxemalar paket ichida (`schemas/`) — `scripts/gen-events.py` nusxalaydi, CI eskirganini ushlaydi.
"""

from __future__ import annotations

import json
from functools import cache, lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from medbron_common.events._catalog_gen import CATALOG


class EventSchemaError(ValueError):
    def __init__(self, event_type: str, errors: list[str]) -> None:
        self.event_type = event_type
        self.errors = errors
        super().__init__(f"{event_type}: {'; '.join(errors[:5])}")


@lru_cache(maxsize=1)
def _registry() -> tuple[Registry, dict[str, dict[str, Any]]]:
    registry: Registry = Registry()
    by_name: dict[str, dict[str, Any]] = {}
    for entry in resources.files("medbron_common.events").joinpath("schemas").iterdir():
        if not entry.name.endswith(".json"):
            continue
        schema = json.loads(entry.read_text(encoding="utf-8"))
        by_name[entry.name] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry, by_name


@cache
def _validator(event_type: str) -> Draft202012Validator:
    spec = CATALOG.get(event_type)
    if spec is None:
        raise EventSchemaError(event_type, ["katalogda yo'q event"])
    registry, by_name = _registry()
    return Draft202012Validator(by_name[spec.schema], registry=registry, format_checker=FormatChecker())


def validate_event(envelope: dict[str, Any]) -> None:
    """Butun envelope (data bilan) sxemaga mosligini tekshiradi; mos bo'lmasa `EventSchemaError`."""
    event_type = envelope.get("event_type")
    if not isinstance(event_type, str):
        raise EventSchemaError(str(event_type), ["event_type yo'q"])
    validator = _validator(event_type)
    errors = sorted(validator.iter_errors(envelope), key=lambda e: list(e.absolute_path))
    if errors:
        raise EventSchemaError(
            event_type, [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]
        )


def event_schema(event_type: str) -> dict[str, Any]:
    spec = CATALOG.get(event_type)
    if spec is None:
        raise EventSchemaError(event_type, ["katalogda yo'q event"])
    return _registry()[1][spec.schema]
