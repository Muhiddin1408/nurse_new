"""Event kontraktlari: konstantalar (`Events`), katalog (`CATALOG`) va JSON Schema validatsiyasi."""

from medbron_common.events._catalog_gen import CATALOG, Events, EventSpec
from medbron_common.events.validation import EventSchemaError, event_schema, validate_event

__all__ = ["CATALOG", "EventSchemaError", "EventSpec", "Events", "event_schema", "validate_event"]
