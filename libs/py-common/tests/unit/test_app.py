from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel

from medbron_common.app import create_app
from medbron_common.config import ServiceSettings
from medbron_common.errors import Conflict
from medbron_common.security import (
    CurrentAdmin,
    CurrentUser,
    SecurityConfig,
    get_current_user,
    require_permission,
    require_roles,
    verify_internal_token,
)
from medbron_common.testing import generate_keypair, mint_admin_token, mint_user_token

USER = generate_keypair()
ADMIN = generate_keypair()


class SlotTaken(Conflict):
    code = "SLOT_TAKEN"
    message = "Bu vaqt allaqachon band qilingan"


class Body(BaseModel):
    count: int


def build_client(*, ready_ok: bool = True) -> TestClient:
    router = APIRouter()

    @router.get("/v1/things/{thing_id}")
    async def get_thing(thing_id: int, user: Annotated[CurrentUser, Depends(get_current_user)]) -> dict:
        if thing_id == 0:
            raise SlotTaken()
        return {"id": thing_id, "user": str(user.user_id)}

    @router.post("/v1/things")
    async def create_thing(body: Body) -> dict:
        return {"count": body.count}

    @router.get("/v1/doctor/only")
    async def doctor_only(user: Annotated[CurrentUser, Depends(require_roles("doctor"))]) -> dict:
        return {"ok": True}

    @router.get("/v1/admin/things")
    async def admin_things(admin: Annotated[CurrentAdmin, Depends(require_permission("things.read"))]) -> dict:
        return {"admin": str(admin.admin_id)}

    @router.get("/internal/v1/things", dependencies=[Depends(verify_internal_token("booking-service"))])
    async def internal_things() -> dict:
        return {"ok": True}

    @router.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("secret detail +998901234567")

    async def ready_check() -> None:
        if not ready_ok:
            raise RuntimeError("db down")

    settings = ServiceSettings(service_name="test-service", environment="test")
    app = create_app(
        settings,
        routers=[router],
        readiness_checks={"db": ready_check},
        security=SecurityConfig.for_tests(
            user_public_pem=USER.public_pem,
            admin_public_pem=ADMIN.public_pem,
            inbound_tokens={"booking-service": "tok"},
        ),
        configure_logs=False,
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client() -> TestClient:
    return build_client()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_live_and_ready(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"] == {"db": "ok"}


def test_ready_fails_with_503() -> None:
    r = build_client(ready_ok=False).get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["db"].startswith("RuntimeError")
    # live DB'ga bog'liq emas
    assert build_client(ready_ok=False).get("/health/live").status_code == 200


def test_metrics_endpoint_records_route_template(client: TestClient) -> None:
    client.get("/v1/things/5", headers=auth(mint_user_token(USER)))
    body = client.get("/metrics").text
    assert "http_requests_total" in body
    assert 'route="/v1/things/{thing_id}"' in body


def test_request_id_is_echoed_or_generated(client: TestClient) -> None:
    r = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    generated = client.get("/health/live").headers["x-request-id"]
    assert len(generated) == 32
    # Zararli qiymat qabul qilinmaydi
    r = client.get("/health/live", headers={"X-Request-ID": "bad value\nwith newline"[:20]})
    assert r.headers["x-request-id"] != "bad value\nwith newl"


def test_unified_error_format(client: TestClient) -> None:
    token = mint_user_token(USER)
    r = client.get("/v1/things/0", headers=auth(token))
    assert r.status_code == 409
    assert r.json() == {"detail": {"code": "SLOT_TAKEN", "message": "Bu vaqt allaqachon band qilingan"}}

    r = client.post("/v1/things", json={"count": "x"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert r.json()["detail"]["errors"][0]["loc"] == ["body", "count"]

    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "HTTP_404"

    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"detail": {"code": "INTERNAL_ERROR", "message": "Ichki xato"}}


def test_user_auth_flow(client: TestClient) -> None:
    assert client.get("/v1/things/1").status_code == 401
    assert client.get("/v1/things/1").json()["detail"]["code"] == "UNAUTHORIZED"
    assert client.get("/v1/things/1", headers=auth(mint_user_token(USER))).status_code == 200
    r = client.get("/v1/things/1", headers=auth(mint_admin_token(ADMIN, permissions=["*"])))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_roles(client: TestClient) -> None:
    assert client.get("/v1/doctor/only", headers=auth(mint_user_token(USER))).status_code == 403
    assert client.get("/v1/doctor/only", headers=auth(mint_user_token(USER, roles=["doctor"]))).status_code == 200


def test_admin_permissions(client: TestClient) -> None:
    ok = mint_admin_token(ADMIN, permissions=["things.read"])
    assert client.get("/v1/admin/things", headers=auth(ok)).status_code == 200
    denied = client.get("/v1/admin/things", headers=auth(mint_admin_token(ADMIN, permissions=["other"])))
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "PERMISSION_DENIED"
    r = client.get("/v1/admin/things", headers=auth(mint_user_token(USER)))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "WRONG_AUDIENCE"


def test_internal_endpoint(client: TestClient) -> None:
    assert client.get("/internal/v1/things").status_code == 401
    ok = client.get("/internal/v1/things", headers={"X-Caller": "booking-service", "X-Internal-Token": "tok"})
    assert ok.status_code == 200
    bad = client.get("/internal/v1/things", headers={"X-Caller": "booking-service", "X-Internal-Token": "nope"})
    assert bad.status_code == 401 and bad.json()["detail"]["code"] == "INTERNAL_AUTH_FAILED"
