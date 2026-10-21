from __future__ import annotations

import uuid
from datetime import timedelta

import jwt
import pytest

from medbron_common.security import (
    CurrentUser,
    JWTVerifier,
    SecurityConfig,
    TokenExpired,
    TokenInvalid,
    WrongAudience,
    load_public_keys,
)
from medbron_common.testing import generate_keypair, mint_admin_token, mint_user_token

USER = generate_keypair()
ADMIN = generate_keypair()
OTHER = generate_keypair()


def user_verifier(*pems: bytes) -> JWTVerifier:
    return JWTVerifier.from_pem(b"\n".join(pems or (USER.public_pem,)), issuer="medbron-auth", audience="medbron")


def test_valid_user_token_roundtrip() -> None:
    uid, did = uuid.uuid4(), uuid.uuid4()
    token = mint_user_token(USER, user_id=uid, roles=["doctor", "client"], doctor_id=did)
    user = CurrentUser.from_claims(user_verifier().verify(token))
    assert user.user_id == uid
    assert user.doctor_id == did
    assert user.role == "doctor"
    assert user.has_role("client") and not user.has_role("clinic_admin")


def test_expired_token() -> None:
    token = mint_user_token(USER, ttl=timedelta(minutes=-5))
    with pytest.raises(TokenExpired):
        user_verifier().verify(token)


def test_admin_token_on_user_endpoint_is_wrong_audience() -> None:
    with pytest.raises(WrongAudience) as exc:
        user_verifier().verify(mint_admin_token(ADMIN, permissions=["*"]))
    assert exc.value.status_code == 403


def test_user_token_on_admin_endpoint_is_wrong_audience() -> None:
    verifier = JWTVerifier.from_pem(ADMIN.public_pem, issuer="medbron-admin", audience="admin")
    with pytest.raises(WrongAudience):
        verifier.verify(mint_user_token(USER))


def test_token_signed_by_unknown_key_rejected() -> None:
    with pytest.raises(TokenInvalid):
        user_verifier().verify(mint_user_token(OTHER))


def test_alg_none_and_hs256_rejected() -> None:
    claims = {"sub": str(uuid.uuid4()), "aud": "medbron", "iss": "medbron-auth", "type": "access"}
    none_token = jwt.encode(claims, key="", algorithm="none")
    with pytest.raises(TokenInvalid):
        user_verifier().verify(none_token)
    # Klassik hujum: public key'ni HMAC siri sifatida ishlatish.
    hs_token = jwt.encode(claims, key="x" * 32, algorithm="HS256")
    with pytest.raises(TokenInvalid):
        user_verifier().verify(hs_token)


def test_refresh_type_rejected_as_access() -> None:
    with pytest.raises(TokenInvalid):
        user_verifier().verify(mint_user_token(USER, token_type="refresh"))


def test_wrong_issuer_rejected() -> None:
    with pytest.raises(TokenInvalid):
        user_verifier().verify(mint_user_token(USER, issuer="evil"))


def test_key_rotation_two_keys_selected_by_kid() -> None:
    verifier = user_verifier(USER.public_pem, OTHER.public_pem)
    assert len(load_public_keys(USER.public_pem + OTHER.public_pem)) == 2
    verifier.verify(mint_user_token(USER))
    verifier.verify(mint_user_token(OTHER))


def test_internal_token_checks() -> None:
    cfg = SecurityConfig.for_tests(inbound_tokens={"booking-service": "s3cret", "review-service": "r3v"})
    assert cfg.check_internal("booking-service", "s3cret", ["booking-service"]) == "booking-service"
    from medbron_common.errors import Forbidden, Unauthorized

    with pytest.raises(Unauthorized):
        cfg.check_internal("booking-service", "wrong", ["booking-service"])
    with pytest.raises(Unauthorized):
        cfg.check_internal("unknown-service", "s3cret", ["booking-service"])
    with pytest.raises(Unauthorized):
        cfg.check_internal(None, None, ["booking-service"])
    # To'g'ri token, lekin bu endpointga ruxsat yo'q.
    with pytest.raises(Forbidden):
        cfg.check_internal("review-service", "r3v", ["booking-service"])


def test_missing_key_file_is_503(tmp_path) -> None:
    from medbron_common.errors import ServiceUnavailable

    cfg = SecurityConfig(user_key_path=tmp_path / "nope.pem")
    with pytest.raises(ServiceUnavailable):
        _ = cfg.user_verifier
