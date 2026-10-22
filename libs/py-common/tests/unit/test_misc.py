import pytest

from medbron_common.config import ServiceSettings
from medbron_common.db import normalize_database_url
from medbron_common.geo import bounding_box, haversine_km


def test_inbound_tokens_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERNAL_TOKEN_BOOKING_SERVICE", "abc")
    monkeypatch.setenv("INTERNAL_TOKEN_WS_GATEWAY", "def")
    s = ServiceSettings(service_name="x")
    tokens = s.inbound_internal_tokens()
    assert tokens["booking-service"].get_secret_value() == "abc"
    assert tokens["ws-gateway"].get_secret_value() == "def"


def test_database_url_docker_wins() -> None:
    s = ServiceSettings(service_name="x", database_url="postgresql://h/db", database_url_docker="postgresql://d/db")
    assert s.effective_database_url == "postgresql://d/db"
    assert normalize_database_url("postgres://u@h/db") == "postgresql+asyncpg://u@h/db"


def test_prod_requires_database_url() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        ServiceSettings(service_name="x", environment="prod")


def test_geo() -> None:
    # Toshkent markazi → Chilonzor ~ 7-8 km
    d = haversine_km(41.3111, 69.2797, 41.2756, 69.2034)
    assert 7 < d < 8
    min_lat, min_lng, max_lat, max_lng = bounding_box(41.3, 69.2, 10)
    assert min_lat < 41.3 < max_lat and min_lng < 69.2 < max_lng


def test_settings_ignore_empty_env_and_optional_env_file(monkeypatch) -> None:
    from pathlib import Path

    from medbron_common.config import ServiceSettings

    class S(ServiceSettings):
        ops_chat: int | None = None
        creds: Path | None = None

    monkeypatch.setenv("OPS_CHAT", "")
    monkeypatch.setenv("CREDS", "")
    s = S(service_name="x")
    assert s.ops_chat is None and s.creds is None
