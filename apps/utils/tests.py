import pytest
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_swagger_sxema_va_ui_ochiladi():
    client = APIClient()

    schema = client.get("/api/schema/", HTTP_ACCEPT="application/vnd.oai.openapi+json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/v1/booking/bookings" in paths
    assert "/api/v1/accounts/auth/otp/verify" in paths
    assert "Idempotency-Key" in str(paths["/api/v1/booking/bookings"])
    assert "jwtAuth" in schema.json()["components"]["securitySchemes"]

    assert client.get("/api/docs/").status_code == 200
