"""2-bosqich ko'prigi: monolit auth-service RS256 tokenini qabul qiladi (api/auth_bridge.py)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework.test import APIClient

from api import auth_bridge
from api.account.services import issue_tokens

PATIENTS = "/api/v1/patient/patients/"


def _keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public_pem, auth_bridge.key_id(private.public_key())


KEY, PUBLIC_PEM, KID = _keypair()
OTHER, _, OTHER_KID = _keypair()


@pytest.fixture(autouse=True)
def bridge(settings, tmp_path):
    path = tmp_path / "jwt-public.pem"
    path.write_bytes(PUBLIC_PEM)
    settings.MEDBRON_JWT_PUBLIC_KEY_FILE = str(path)
    auth_bridge._keys.cache_clear()
    yield
    auth_bridge._keys.cache_clear()


def token(sub, *, phone="+998901112233", key=KEY, kid=KID, aud="medbron", ttl=900, typ="access", role="client"):
    now = datetime.now(UTC)
    claims = {"sub": str(sub), "role": role, "roles": ["client", role], "phone": phone, "iss": "medbron-auth",
              "aud": aud, "type": typ, "iat": now, "exp": now + timedelta(seconds=ttl), "jti": uuid.uuid4().hex}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def call(tok):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tok}")
    return api.get(PATIENTS)


@pytest.mark.django_db
def test_existing_user_accepted_with_same_uuid():
    from apps.account.models import User

    user = User.objects.create_user(phone="+998901112233")
    assert call(token(user.id)).status_code == 200


@pytest.mark.django_db
def test_new_auth_service_user_is_provisioned_once():
    from apps.account.models import User

    uid = uuid.uuid4()
    assert call(token(uid, phone="+998935556677")).status_code == 200
    assert call(token(uid, phone="+998935556677")).status_code == 200
    user = User.objects.get(id=uid)
    assert user.phone == "+998935556677" and user.is_phone_verified and not user.has_usable_password()


@pytest.mark.django_db
def test_phone_taken_by_other_uuid_is_rejected():
    from apps.account.models import User

    User.objects.create_user(phone="+998901112233")
    assert call(token(uuid.uuid4())).status_code == 401


@pytest.mark.django_db
def test_rejections():
    from apps.account.models import User

    user = User.objects.create_user(phone="+998901112233")
    assert call(token(user.id, ttl=-60)).status_code == 401  # muddati o'tgan
    assert call(token(user.id, aud="admin")).status_code == 401
    assert call(token(user.id, typ="refresh")).status_code == 401
    assert call(token(user.id, key=OTHER, kid=OTHER_KID)).status_code == 401  # noma'lum kid
    assert call(token(user.id, key=OTHER, kid=KID)).status_code == 401  # soxta imzo
    User.objects.filter(id=user.id).update(is_active=False)
    assert call(token(user.id)).status_code == 401


@pytest.mark.django_db
def test_legacy_hs256_tokens_still_work_and_role_claim_mapped():
    from apps.account.models import User

    user = User.objects.create_user(phone="+998901112233")
    legacy = issue_tokens(user)["access"]
    assert call(legacy).status_code == 200

    from rest_framework.test import APIRequestFactory

    request = APIRequestFactory().get(PATIENTS, HTTP_AUTHORIZATION=f"Bearer {token(user.id, role='doctor')}")
    authed_user, claims = auth_bridge.AuthServiceTokenAuthentication().authenticate(request)
    assert authed_user == user and claims["active_role"] == "doctor"


@pytest.mark.django_db
def test_bridge_disabled_without_key(settings):
    settings.MEDBRON_JWT_PUBLIC_KEY_FILE = ""
    auth_bridge._keys.cache_clear()
    assert call(token(uuid.uuid4())).status_code == 401  # simplejwt RS256 ni qabul qilmaydi
