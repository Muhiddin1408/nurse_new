"""Python ↔ Go umumiy test vektorlari (libs/testdata). Go tomonida xuddi shu fayllar tekshiriladi."""

import json
from pathlib import Path

from medbron_common.events import validate_event
from medbron_common.security import load_public_keys

TESTDATA = Path(__file__).resolve().parents[3] / "testdata"


def test_kid_vector() -> None:
    v = json.loads((TESTDATA / "kid-vector.json").read_text())
    assert v["kid"] in load_public_keys(v["public_pem"].encode())


def test_envelope_vector_valid() -> None:
    validate_event(json.loads((TESTDATA / "envelope-vector.json").read_text())["envelope"])
