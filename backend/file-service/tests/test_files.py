"""Fayl hayot sikli haqiqiy Postgres + S3 (SeaweedFS) bilan. Docker talab qiladi: `pytest -m integration`."""

from __future__ import annotations

import io
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from medbron_common.testing import assert_event_valid, mint_admin_token, mint_user_token

pytestmark = pytest.mark.integration

INTERNAL = {"X-Caller": "user-service", "X-Internal-Token": "test-token"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def jpeg_with_gps(size: tuple[int, int] = (640, 480)) -> bytes:
    img = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SecretPhone"  # Make
    exif[0x8825] = {2: (41.0, 18.0, 0.0)}  # GPSInfo — tozalanishi SHART
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def upload(api: TestClient, token: str, purpose: str, content_type: str, data: bytes, name: str = "f") -> dict:
    r = api.post(
        "/v1/files/uploads",
        json={"purpose": purpose, "content_type": content_type, "size": len(data), "filename": name},
        headers=bearer(token),
    )
    assert r.status_code == 201, r.text
    ticket = r.json()
    put = httpx.put(ticket["upload_url"], content=data, headers=ticket["headers"])
    assert put.status_code == 200, put.text
    return dict(ticket)


def audits(db) -> list[dict[str, Any]]:
    rows = db.all("SELECT payload FROM outbox_events WHERE event_type = 'audit.recorded' ORDER BY id")
    for (payload,) in rows:
        assert_event_valid(payload)
    return [row[0]["data"] for row in rows]


def test_avatar_lifecycle_strips_exif_and_makes_thumbnail(api: TestClient, db, user_keys) -> None:
    uid = uuid.uuid4()
    token = mint_user_token(user_keys, user_id=uid)
    ticket = upload(api, token, "avatar", "image/jpeg", jpeg_with_gps(), "../../etc/men <script>.jpg")

    r = api.post(f"/v1/files/{ticket['file_id']}/complete", headers=bearer(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "uploaded" and body["visibility"] == "public"
    assert ".." not in body["original_name"] and "<" not in body["original_name"]
    assert str(uid) not in body["url"]  # kalitda user ID yo'q

    stored = httpx.get(body["url"])  # ommaviy — anonim o'qiladi
    assert stored.status_code == 200
    with Image.open(io.BytesIO(stored.content)) as img:
        exif = img.getexif()
        assert 0x8825 not in exif and 0x010F not in exif
    thumb = httpx.get(body["thumbnail_url"])
    with Image.open(io.BytesIO(thumb.content)) as img:
        assert img.format == "WEBP" and max(img.size) <= 256

    # complete idempotent
    assert api.post(f"/v1/files/{ticket['file_id']}/complete", headers=bearer(token)).json()["status"] == "uploaded"
    # Boshqa foydalanuvchiga — xuddi yo'qdek
    other = mint_user_token(user_keys)
    assert api.get(f"/v1/files/{ticket['file_id']}", headers=bearer(other)).status_code == 404

    # user-service biriktiradi (idempotent), boshqa user nomidan — rad
    attach = {"owner_user_id": str(uid), "owner_ref": f"user:{uid}:avatar", "purpose": "avatar"}
    r = api.post(f"/internal/v1/files/{ticket['file_id']}/attach", json=attach, headers=INTERNAL)
    assert r.status_code == 200 and r.json()["status"] == "attached" and r.json()["url"] == body["url"]
    assert api.post(f"/internal/v1/files/{ticket['file_id']}/attach", json=attach, headers=INTERNAL).status_code == 200
    stranger = {**attach, "owner_user_id": str(uuid.uuid4())}
    assert (
        api.post(f"/internal/v1/files/{ticket['file_id']}/attach", json=stranger, headers=INTERNAL).status_code == 404
    )
    other_ref = {**attach, "owner_ref": "user:x:avatar"}
    assert (
        api.post(f"/internal/v1/files/{ticket['file_id']}/attach", json=other_ref, headers=INTERNAL).status_code == 409
    )
    # Biriktirilgan faylni egasi to'g'ridan-to'g'ri o'chira olmaydi
    assert api.delete(f"/v1/files/{ticket['file_id']}", headers=bearer(token)).status_code == 409

    r = api.post(
        f"/internal/v1/files/{ticket['file_id']}/release", json={"owner_ref": f"user:{uid}:avatar"}, headers=INTERNAL
    )
    assert r.status_code == 204
    assert httpx.get(body["url"]).status_code == 404
    assert db.scalar("SELECT status FROM files WHERE id = :i", i=ticket["file_id"]) == "deleted"


def test_rejects_wrong_type_size_and_purpose(api: TestClient, db, user_keys) -> None:
    token = mint_user_token(user_keys)
    r = api.post("/v1/files/uploads", json={"purpose": "nope", "content_type": "image/png", "size": 10},
                 headers=bearer(token))  # fmt: skip
    assert r.status_code == 422 and r.json()["detail"]["code"] == "UNKNOWN_PURPOSE"
    r = api.post("/v1/files/uploads", json={"purpose": "avatar", "content_type": "application/pdf", "size": 10},
                 headers=bearer(token))  # fmt: skip
    assert r.status_code == 422 and r.json()["detail"]["code"] == "CONTENT_TYPE_NOT_ALLOWED"
    r = api.post("/v1/files/uploads", json={"purpose": "avatar", "content_type": "image/png", "size": 6 * 1024 * 1024},
                 headers=bearer(token))  # fmt: skip
    assert r.status_code == 422 and r.json()["detail"]["code"] == "FILE_TOO_LARGE"

    # .png deb e'lon qilingan HTML — mazmun bo'yicha rad etiladi va obyekt o'chiriladi
    ticket = upload(api, token, "avatar", "image/png", b"<html><script>alert(1)</script></html>")
    r = api.post(f"/v1/files/{ticket['file_id']}/complete", headers=bearer(token))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "FILE_REJECTED"
    assert db.scalar("SELECT reject_reason FROM files WHERE id = :i", i=ticket["file_id"]) == "type_mismatch"

    # Imzolangan Content-Type'dan boshqasi bilan yuklab bo'lmaydi
    r = api.post("/v1/files/uploads", json={"purpose": "avatar", "content_type": "image/png", "size": 100},
                 headers=bearer(token))  # fmt: skip
    put = httpx.put(r.json()["upload_url"], content=b"x" * 100, headers={"Content-Type": "text/html"})
    assert put.status_code == 403
    # Yuklanmagan faylni complete — 409
    r2 = api.post(f"/v1/files/{r.json()['file_id']}/complete", headers=bearer(token))
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "UPLOAD_MISSING"


def test_private_document_short_url_and_audit(api: TestClient, db, user_keys, admin_keys) -> None:
    uid = uuid.uuid4()
    token = mint_user_token(user_keys, user_id=uid, roles=["client", "doctor"])
    ticket = upload(api, token, "doctor_document", "application/pdf", pdf(), "diplom.pdf")
    r = api.post(f"/v1/files/{ticket['file_id']}/complete", headers=bearer(token))
    assert r.status_code == 200 and r.json()["url"] is None and r.json()["thumbnail_url"] is None

    signed = api.get(f"/v1/files/{ticket['file_id']}/url", headers=bearer(token)).json()
    assert 0 < signed["expires_in"] <= 300
    assert httpx.get(signed["url"]).content.startswith(b"%PDF")
    key = db.scalar("SELECT object_key FROM files WHERE id = :i", i=ticket["file_id"])
    from app.core.config import get_settings

    anon = httpx.get(f"{get_settings().s3_public_endpoint_url}/private-documents/{key}")
    assert anon.status_code == 403

    # user-service bu turdagi faylni biriktira olmaydi
    attach = {"owner_user_id": str(uid), "owner_ref": "doctor:1:diploma"}
    r = api.post(f"/internal/v1/files/{ticket['file_id']}/attach", json=attach, headers=INTERNAL)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "ATTACH_NOT_ALLOWED"
    catalog = {"X-Caller": "catalog-service", "X-Internal-Token": "test-token"}
    assert api.post(f"/internal/v1/files/{ticket['file_id']}/attach", json=attach, headers=catalog).status_code == 200
    r = api.get(
        f"/internal/v1/files/{ticket['file_id']}/url",
        params={"ttl": 120, "viewer_type": "admin", "viewer_id": "a1"},
        headers=catalog,
    )
    assert r.status_code == 200 and r.json()["expires_in"] == 120
    assert api.get(f"/internal/v1/files/{ticket['file_id']}/url", headers=INTERNAL).status_code == 404

    admin = mint_admin_token(admin_keys, permissions=["files.read"])
    r = api.get(f"/v1/admin/files/{ticket['file_id']}/url", headers=bearer(admin))
    assert r.status_code == 200 and r.json()["expires_in"] == 300
    assert api.get(f"/v1/admin/files/{ticket['file_id']}", headers=bearer(admin)).json()["status"] == "attached"
    no = mint_admin_token(admin_keys, permissions=["users.manage"])
    assert api.get(f"/v1/admin/files/{ticket['file_id']}/url", headers=bearer(no)).status_code == 403

    views = [a for a in audits(db) if a["action"] == "file.view"]
    assert [v["actor_type"] for v in views] == ["user", "system", "admin"]
    assert views[1]["after"]["viewer_id"] == "a1" and views[1]["actor_role"] == "catalog-service"


def test_pending_limit_owner_delete_and_cleanup(api: TestClient, db, user_keys, monkeypatch) -> None:
    import asyncio

    from tests.conftest import reset_caches

    token = mint_user_token(user_keys)
    ticket = upload(api, token, "avatar", "image/jpeg", jpeg_with_gps((64, 64)))
    assert api.delete(f"/v1/files/{ticket['file_id']}", headers=bearer(token)).status_code == 204
    assert api.get(f"/v1/files/{ticket['file_id']}", headers=bearer(token)).status_code == 404

    monkeypatch.setenv("MAX_PENDING_UPLOADS_PER_USER", "2")
    reset_caches()
    from app.main import build_app

    with TestClient(build_app()) as limited:
        limited.app.state.security = api.app.state.security
        for _ in range(2):
            r = limited.post("/v1/files/uploads", json={"purpose": "avatar", "content_type": "image/png", "size": 9},
                             headers=bearer(token))  # fmt: skip
            assert r.status_code == 201
        r = limited.post("/v1/files/uploads", json={"purpose": "avatar", "content_type": "image/png", "size": 9},
                         headers=bearer(token))  # fmt: skip
        assert r.status_code == 429 and r.json()["detail"]["code"] == "TOO_MANY_PENDING_UPLOADS"
    monkeypatch.delenv("MAX_PENDING_UPLOADS_PER_USER")
    reset_caches()

    # 24 soatdan eski biriktirilmagan fayllar — scheduler o'chiradi (obyekt ham)
    stale = upload(api, token, "avatar", "image/jpeg", jpeg_with_gps((32, 32)))
    db.scalar("UPDATE files SET created_at = now() - interval '25 hours' WHERE status = 'pending'")
    reset_caches()
    from app.core.database import get_engine
    from app.workers.scheduler import cleanup

    async def run() -> int:
        try:
            return await cleanup()
        finally:
            await get_engine().dispose()

    assert asyncio.run(run()) == 3
    assert db.scalar("SELECT count(*) FROM files WHERE status = 'pending'") == 0
    assert db.scalar("SELECT status FROM files WHERE id = :i", i=stale["file_id"]) == "deleted"


def test_internal_requires_allowed_caller(api: TestClient) -> None:
    r = api.post(
        f"/internal/v1/files/{uuid.uuid4()}/attach",
        json={"owner_user_id": str(uuid.uuid4()), "owner_ref": "x"},
        headers={"X-Caller": "test-caller", "X-Internal-Token": "test-token"},
    )
    assert r.status_code == 403 and r.json()["detail"]["code"] == "CALLER_NOT_ALLOWED"
