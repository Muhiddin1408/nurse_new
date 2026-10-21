"""Bazasiz testlar: health, auth zanjiri, purpose katalogi va magic bytes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from medbron_common.testing import mint_admin_token, mint_user_token

from app.core.purposes import PDF, PNG, PURPOSES, sniff
from app.services.files import safe_filename


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_metrics(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200
    assert "http_requests_total" in client.get("/metrics").text


def test_auth_chain(client: TestClient, user_keys, admin_keys) -> None:
    assert client.post("/v1/files/uploads", json={}).status_code in (401, 422)
    r = client.get("/v1/admin/files/00000000-0000-0000-0000-000000000000",
                   headers=bearer(mint_user_token(user_keys)))  # fmt: skip
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"
    r = client.get("/v1/admin/files/00000000-0000-0000-0000-000000000000/url",
                   headers=bearer(mint_admin_token(admin_keys, permissions=["x"])))  # fmt: skip
    assert r.status_code == 403 and r.json()["detail"]["permission"] == "files.read"


def test_sniff_and_names() -> None:
    assert sniff(b"\x89PNG\r\n\x1a\nrest") == PNG
    assert sniff(b"%PDF-1.7") == PDF
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff(b"<html>") is None
    assert safe_filename("../../etc/men <script>.jpg") == "men _script_.jpg"
    assert safe_filename("C:\\x\\..evil..pdf") == "evil.pdf"
    assert safe_filename("   ") == "file" and safe_filename("..") == "file"
    assert all(p.max_bytes <= 15 * 1024 * 1024 for p in PURPOSES.values())
    assert {p.visibility for p in PURPOSES.values()} == {"public", "private"}
