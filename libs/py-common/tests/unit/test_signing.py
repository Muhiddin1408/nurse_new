from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from medbron_common.security import JWTVerifier, TokenExpired
from medbron_common.signing import TokenSigner
from medbron_common.testing import generate_keypair


def test_sign_verifies_with_public_key_and_jwks(tmp_path: Path) -> None:
    keys, old = generate_keypair(), generate_keypair()
    (tmp_path / "priv.pem").write_bytes(keys.private_pem)
    (tmp_path / "pub.pem").write_bytes(old.public_pem + keys.public_pem)
    signer = TokenSigner.from_files(
        tmp_path / "priv.pem", tmp_path / "pub.pem", issuer="medbron-admin", audience="admin", ttl=timedelta(minutes=30)
    )
    assert signer.kid == keys.kid
    uid = uuid.uuid4()
    token = signer.sign(uid, {"permissions": ["users.manage"]})
    claims = JWTVerifier.from_pem(keys.public_pem, issuer="medbron-admin", audience="admin").verify(token)
    assert claims["sub"] == str(uid) and claims["permissions"] == ["users.manage"] and claims["type"] == "access"
    assert claims["exp"] - claims["iat"] == 1800
    assert {k["kid"] for k in signer.jwks()["keys"]} == {keys.kid, old.kid}


def test_expired_and_missing_key(tmp_path: Path) -> None:
    keys = generate_keypair()
    signer = TokenSigner.from_pem(keys.private_pem, issuer="i", audience="a", ttl=timedelta(seconds=-60))
    with pytest.raises(TokenExpired):
        JWTVerifier.from_pem(keys.public_pem, issuer="i", audience="a").verify(signer.sign("x", {}))
    with pytest.raises(RuntimeError, match="topilmadi"):
        TokenSigner.from_files(tmp_path / "missing.pem", issuer="i", audience="a", ttl=timedelta(minutes=1))
    with pytest.raises(ValueError):
        TokenSigner.from_pem(
            b"-----BEGIN PRIVATE KEY-----\nAA==\n-----END PRIVATE KEY-----\n",
            issuer="i",
            audience="a",
            ttl=timedelta(minutes=1),
        )


def test_read_private_pem_inline_and_file(tmp_path: Path) -> None:
    import base64

    from pydantic import SecretStr

    from medbron_common.signing import read_private_pem

    keys = generate_keypair()
    assert read_private_pem(SecretStr(base64.b64encode(keys.private_pem).decode()), tmp_path / "x") == keys.private_pem
    assert read_private_pem(SecretStr(keys.private_pem.decode()), tmp_path / "x").strip() == keys.private_pem.strip()
    (tmp_path / "k.pem").write_bytes(keys.private_pem)
    assert read_private_pem(SecretStr("  "), tmp_path / "k.pem") == keys.private_pem
    with pytest.raises(RuntimeError, match="topilmadi"):
        read_private_pem(None, tmp_path / "missing.pem")
