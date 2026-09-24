"""A6: refresh + logout + logout/all."""

import pytest
from rest_framework.test import APIClient

from api.account.services import issue_tokens


@pytest.fixture
def user(db):
    from apps.account.models import User

    return User.objects.create_user(phone="+998901112233")


@pytest.mark.django_db
def test_refresh_ishlaydi_va_rotation_eskisini_yaroqsiz_qiladi(user):
    tokens = issue_tokens(user)
    api = APIClient()

    resp = api.post("/api/v1/accounts/auth/token/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert resp.status_code == 200
    assert "access" in resp.json()

    reused = api.post("/api/v1/accounts/auth/token/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert reused.status_code == 401


@pytest.mark.django_db
def test_logoutdan_keyin_refresh_ishlamaydi(user):
    tokens = issue_tokens(user)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    assert api.post("/api/v1/accounts/auth/logout", {"refresh": tokens["refresh"]}, format="json").status_code == 204

    api.credentials()
    resp = api.post("/api/v1/accounts/auth/token/refresh", {"refresh": tokens["refresh"]}, format="json")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_begona_refresh_bilan_logout_400(user):
    from apps.account.models import User

    other = User.objects.create_user(phone="+998901114455")
    api = APIClient()
    api.force_authenticate(user)
    resp = api.post("/api/v1/accounts/auth/logout", {"refresh": issue_tokens(other)["refresh"]}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_logout_all_barcha_qurilmalarni_chiqaradi(user):
    phone_a, phone_b = issue_tokens(user), issue_tokens(user)
    api = APIClient()
    api.force_authenticate(user)
    assert api.post("/api/v1/accounts/auth/logout/all").status_code == 204

    api.force_authenticate(None)
    for t in (phone_a, phone_b):
        resp = api.post("/api/v1/accounts/auth/token/refresh", {"refresh": t["refresh"]}, format="json")
        assert resp.status_code == 401
