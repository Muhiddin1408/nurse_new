"""Tuzatilgan kamchiliklar qaytib kelmasligi uchun statik to'siqlar ("uchta umumiy qoida", 1-qoida).

A4 — serializerda global queryset'li PrimaryKeyRelatedField yo'q (IDOR).
C8 — `timezone.now().strftime` yo'q: foydalanuvchiga ko'rinadigan sana `localtime` bilan.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIRS = ("api", "apps", "analytics")


def _python_sources():
    for d in SOURCE_DIRS:
        for path in (ROOT / d).rglob("*.py"):
            if "migrations" in path.parts or path.name.startswith("test"):
                continue
            yield path


def test_global_querysetli_primary_key_related_field_yoq():
    pattern = re.compile(r"PrimaryKeyRelatedField\([^)]*queryset=[^)]*objects\.all\(\)")
    offenders = [str(p.relative_to(ROOT)) for p in _python_sources() if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], f"A4: egalik loader'idan foydalaning: {offenders}"


def test_utc_now_strftime_yoq():
    pattern = re.compile(r"now\(\)\.strftime|timezone\.localdate\(\)")
    offenders = [str(p.relative_to(ROOT)) for p in _python_sources() if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], f"C8: timezone.localtime(...) ishlating: {offenders}"
